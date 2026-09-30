"""flight_rules: does the sequence break a flight rule?

The bytecode is decoded with the dictionary the target vehicle is running, because that is
how the vehicle will read it. If a command's argument bytes do not fit that dictionary, the
check blocks as well: the vehicle would not read the command the way it was written.
"""

from __future__ import annotations

from gate.bytecode import BytecodeError, load_model
from gate.checks import CheckContext, blocked, passed, skipped
from gate.results import CheckResult, Reason
from gate.rules import Rule, evaluate_rule

NAME = "flight_rules"


def flight_rules(ctx: CheckContext) -> CheckResult:
    running = ctx.running_dictionary_hash
    if running is None:
        return blocked(
            NAME,
            Reason.RUNNING_DICTIONARY_UNRESOLVED,
            "flight rules are evaluated against the dictionary the vehicle is running, "
            "and that is not known",
        )
    dictionary = ctx.registry.get_dictionary(running)
    assert dictionary is not None

    rules = [
        Rule.from_json(row.id, row.rule_json, row.dictionary_hash)
        for row in ctx.registry.flight_rules_for(running)
    ]

    try:
        model = load_model(ctx.bytecode_path, dictionary.path)
    except BytecodeError as error:
        return blocked(NAME, Reason.BYTECODE_UNREADABLE, str(error))
    ctx.scratch["sequence_model"] = model

    unreadable = [c for c in model.commands if c.problem is not None and not c.dynamic]
    if unreadable:
        first = unreadable[0]
        return blocked(
            NAME,
            Reason.BYTECODE_UNREADABLE,
            f"directive {first.index} ({first.label}) does not decode with dictionary "
            f"{running[:12]} ({dictionary.project_version}): {first.problem}",
            undecodable=[
                {"directive_index": c.index, "command": c.label, "problem": c.problem}
                for c in unreadable
            ],
        )

    if not rules:
        return skipped(NAME, Reason.NO_FLIGHT_RULES, "no flight rules are loaded")

    violations = [v for rule in rules for v in evaluate_rule(rule, model)]
    if violations:
        broken = sorted({v.rule_id for v in violations})
        return blocked(
            NAME,
            Reason.FLIGHT_RULE_VIOLATION,
            f"{len(violations)} violation(s) of {', '.join(broken)}: {violations[0].message}",
            violations=[v.to_dict() for v in violations],
            rules_checked=[r.id for r in rules],
        )
    return passed(
        NAME,
        f"{len(rules)} rule(s) hold for the {len(model.commands)} command(s) in the sequence",
        rules_checked=[r.id for r in rules],
        commands=[c.label for c in model.commands],
    )
