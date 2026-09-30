"""Flight rules: a small declarative format, checked statically against a sequence.

A flight rule is a documented way the vehicle must not be operated. The three rule types
here follow the archetypes JPL's FRESH flight rule checker describes
(https://github.com/NASA-JPL-Teamtools-Studio/tts_fresh): do not send command X, do not
send command X with arguments Y, and do not send command X within T seconds of command Z.
This is a much smaller thing than FRESH and makes no claim to be new.

```yaml
rules:
  - id: no-param-save
    type: forbidden_command
    command: "FileHandling.prmDb.PRM_SAVE_FILE"     # shell-style patterns are allowed
    description: Parameter saves are commanded by hand, not from sequences.

  - id: sg-frequency
    type: argument_range
    command: "Ref.SG*.Settings"
    argument: Frequency
    min: 1
    max: 12

  - id: settle-before-toggle
    type: min_separation
    first: "Ref.SG1.Settings"
    second: "Ref.SG1.Toggle"
    seconds: 2
    dictionary_hash: 4bbb...      # optional: the rule applies only to this dictionary
```

Like FRESH, this does not simulate the sequence. A rule that needs a value computed while
the sequence runs cannot be verified, and that is reported as a violation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml

from gate.bytecode import CommandCall, SequenceModel

RULE_TYPES = ("forbidden_command", "argument_range", "min_separation")


class RuleError(ValueError):
    """A rule definition is malformed."""


@dataclass(frozen=True)
class Rule:
    id: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    dictionary_hash: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"type": self.type, "description": self.description, **self.params}

    @classmethod
    def from_json(cls, rule_id: str, data: dict[str, Any], dictionary_hash: str | None) -> Rule:
        params = {k: v for k, v in data.items() if k not in ("type", "description")}
        return cls(rule_id, data["type"], params, data.get("description", ""), dictionary_hash)


@dataclass(frozen=True)
class Violation:
    rule_id: str
    message: str
    directive_index: int | None = None
    command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "directive_index": self.directive_index,
            "command": self.command,
        }


_REQUIRED = {
    "forbidden_command": ("command",),
    "argument_range": ("command", "argument"),
    "min_separation": ("first", "second", "seconds"),
}
_OPTIONAL = {
    "forbidden_command": (),
    "argument_range": ("min", "max", "allowed"),
    "min_separation": (),
}


def parse_rules(document: Any) -> list[Rule]:
    """Validate a parsed rules document and return its rules."""
    if not isinstance(document, dict) or not isinstance(document.get("rules"), list):
        raise RuleError("a rules file needs a top-level 'rules' list")
    rules: list[Rule] = []
    seen: set[str] = set()
    for position, entry in enumerate(document["rules"]):
        if not isinstance(entry, dict):
            raise RuleError(f"rule #{position + 1} is not a mapping")
        rule_id = str(entry.get("id", "")).strip()
        if not rule_id:
            raise RuleError(f"rule #{position + 1} has no id")
        if rule_id in seen:
            raise RuleError(f"rule id '{rule_id}' is used twice")
        seen.add(rule_id)
        rule_type = entry.get("type")
        if rule_type not in RULE_TYPES:
            raise RuleError(f"rule '{rule_id}': type must be one of {', '.join(RULE_TYPES)}")
        missing = [key for key in _REQUIRED[rule_type] if key not in entry]
        if missing:
            raise RuleError(f"rule '{rule_id}' is missing {', '.join(missing)}")
        known = {"id", "type", "description", "dictionary_hash"}
        known |= set(_REQUIRED[rule_type]) | set(_OPTIONAL[rule_type])
        unknown = sorted(set(entry) - known)
        if unknown:
            raise RuleError(f"rule '{rule_id}' has unknown keys: {', '.join(unknown)}")
        params = {k: entry[k] for k in (*_REQUIRED[rule_type], *_OPTIONAL[rule_type]) if k in entry}
        if rule_type == "argument_range" and not {"min", "max", "allowed"} & set(params):
            raise RuleError(f"rule '{rule_id}' needs at least one of min, max, allowed")
        if rule_type == "min_separation" and not isinstance(params["seconds"], int | float):
            raise RuleError(f"rule '{rule_id}': seconds must be a number")
        dictionary_hash = entry.get("dictionary_hash")
        rules.append(
            Rule(
                id=rule_id,
                type=rule_type,
                params=params,
                description=str(entry.get("description", "")),
                dictionary_hash=str(dictionary_hash) if dictionary_hash else None,
            )
        )
    return rules


def load_rules_file(path: Path | str) -> list[Rule]:
    try:
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RuleError(f"cannot read rules file {path}: {error}") from error
    return parse_rules(document)


def _matching(model: SequenceModel, pattern: str) -> list[CommandCall]:
    return [c for c in model.commands if c.name is not None and fnmatchcase(c.name, pattern)]


def evaluate_rule(rule: Rule, model: SequenceModel) -> list[Violation]:
    """Return every way the sequence breaks the rule. Empty means the rule holds."""
    if rule.type == "forbidden_command":
        return [
            Violation(
                rule.id,
                f"{call.name} is forbidden in sequences",
                call.index,
                call.name,
            )
            for call in _matching(model, rule.params["command"])
        ]
    if rule.type == "argument_range":
        return _argument_range(rule, model)
    if rule.type == "min_separation":
        return _min_separation(rule, model)
    raise RuleError(f"rule '{rule.id}' has unknown type {rule.type}")


def _argument_range(rule: Rule, model: SequenceModel) -> list[Violation]:
    argument = rule.params["argument"]
    low, high = rule.params.get("min"), rule.params.get("max")
    allowed = rule.params.get("allowed")
    violations: list[Violation] = []
    for call in _matching(model, rule.params["command"]):

        def violation(message: str, call: CommandCall = call) -> Violation:
            return Violation(rule.id, message, call.index, call.name)

        if call.args is None:
            why = "it is computed while the sequence runs" if call.dynamic else call.problem
            violations.append(violation(f"cannot verify {argument} of {call.name}: {why}"))
            continue
        if argument not in call.args:
            violations.append(violation(f"{call.name} has no argument named {argument}"))
            continue
        value = call.args[argument]
        if allowed is not None and value not in allowed:
            violations.append(
                violation(f"{call.name} {argument}={value!r} is not one of {allowed}")
            )
        if low is not None or high is not None:
            if isinstance(value, bool) or not isinstance(value, int | float):
                violations.append(violation(f"{call.name} {argument}={value!r} is not a number"))
                continue
            if low is not None and value < low:
                violations.append(violation(f"{call.name} {argument}={value} is below {low}"))
            if high is not None and value > high:
                violations.append(violation(f"{call.name} {argument}={value} is above {high}"))
    return violations


def _min_separation(rule: Rule, model: SequenceModel) -> list[Violation]:
    required = float(rule.params["seconds"])
    firsts = _matching(model, rule.params["first"])
    seconds = _matching(model, rule.params["second"])
    violations: list[Violation] = []
    for second in seconds:
        looping = [
            f for f in firsts if f.index != second.index and model.loops_over(f.index, second.index)
        ]
        if looping:
            violations.append(
                Violation(
                    rule.id,
                    f"{second.name} and {looping[0].name} are inside the same loop, so "
                    f"{required:g} s between them cannot be shown",
                    second.index,
                    second.name,
                )
            )
            continue
        before = [f for f in firsts if f.index < second.index]
        if not before:
            continue
        first = before[-1]
        gap = model.min_seconds_between(first.index, second.index)
        if gap < required:
            violations.append(
                Violation(
                    rule.id,
                    f"{second.name} follows {first.name} after at least {gap:g} s, "
                    f"but {required:g} s are required",
                    second.index,
                    second.name,
                )
            )
    return violations
