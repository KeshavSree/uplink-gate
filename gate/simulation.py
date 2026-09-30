"""A very small state simulation of a sequence, starting from live telemetry.

Static flight rules cannot express "do not send command X in condition Y" when Y is a
state of the vehicle. This module predicts a handful of modeled state variables: it takes
their starting values from the latest telemetry, walks the sequence's commands in order,
applies each command's declared effect, and checks constraints against the predicted state
just before each command.

The model is deliberately tiny and explicit. It is a YAML file:

```yaml
simulation:
  state:
    sg1_running:
      # true if this channel was received within the last N seconds
      initial: {channel_active: "Ref.SG1.Output", within_seconds: 3}
    sg1_type:
      # the latest value of this channel
      initial: {channel: "Ref.SG1.Type"}
  effects:
    - command: "Ref.SG1.Toggle"
      toggle: sg1_running
    - command: "Ref.SG1.Settings"
      set: {sg1_type: "$SigType"}       # "$Name" means the command's argument Name
  constraints:
    - id: sg1-settings-only-while-stopped
      before: "Ref.SG1.Settings"
      require: {sg1_running: false}
```

What it assumes, and says so in its results: every command succeeds, commands run in the
order they appear, and nothing else changes the modeled state meanwhile. A sequence that
loops or branches on telemetry cannot be predicted this way and is reported as such.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml

from gate.bytecode import CommandCall, SequenceModel

#: A state value the simulation cannot determine.
UNKNOWN = "unknown"


class ModelError(ValueError):
    """The simulation model file is malformed."""


@dataclass(frozen=True)
class Sample:
    """The latest reading of one telemetry channel."""

    value: Any
    received_at: datetime


Telemetry = Mapping[str, Sample]


@dataclass(frozen=True)
class Effect:
    command: str
    set: dict[str, Any] = field(default_factory=dict)
    toggle: tuple[str, ...] = ()


@dataclass(frozen=True)
class Constraint:
    id: str
    before: str
    require: dict[str, Any]
    description: str = ""


@dataclass(frozen=True)
class SimulationModel:
    initial: dict[str, dict[str, Any]]
    effects: tuple[Effect, ...]
    constraints: tuple[Constraint, ...]

    @property
    def variables(self) -> tuple[str, ...]:
        return tuple(self.initial)


@dataclass(frozen=True)
class Step:
    """The predicted state after one command."""

    directive_index: int
    command: str
    state: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "directive_index": self.directive_index,
            "command": self.command,
            "state": self.state,
        }


@dataclass(frozen=True)
class SimulationViolation:
    constraint_id: str
    message: str
    directive_index: int | None = None
    command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "message": self.message,
            "directive_index": self.directive_index,
            "command": self.command,
        }


@dataclass(frozen=True)
class SimulationResult:
    initial_state: dict[str, Any]
    steps: tuple[Step, ...]
    violations: tuple[SimulationViolation, ...]
    #: Set when the sequence cannot be predicted at all.
    unpredictable: str | None = None

    @property
    def final_state(self) -> dict[str, Any]:
        return self.steps[-1].state if self.steps else self.initial_state


def parse_model(document: Any) -> SimulationModel:
    body = document.get("simulation") if isinstance(document, dict) else None
    if not isinstance(body, dict) or not isinstance(body.get("state"), dict):
        raise ModelError("a simulation model needs 'simulation.state'")
    initial: dict[str, dict[str, Any]] = {}
    for name, spec in body["state"].items():
        source = spec.get("initial") if isinstance(spec, dict) else None
        if not isinstance(source, dict) or not (
            {"channel", "channel_active", "value"} & set(source)
        ):
            raise ModelError(
                f"state '{name}' needs initial.channel, initial.channel_active or initial.value"
            )
        initial[str(name)] = source

    effects: list[Effect] = []
    for entry in body.get("effects", []):
        if not isinstance(entry, dict) or "command" not in entry:
            raise ModelError("every effect needs a 'command'")
        sets = entry.get("set", {})
        toggles = entry.get("toggle", [])
        toggles = [toggles] if isinstance(toggles, str) else list(toggles)
        for variable in [*sets, *toggles]:
            if variable not in initial:
                raise ModelError(f"effect of {entry['command']} changes unknown state '{variable}'")
        effects.append(Effect(str(entry["command"]), dict(sets), tuple(toggles)))

    constraints: list[Constraint] = []
    for entry in body.get("constraints", []):
        if not isinstance(entry, dict) or not {"id", "before", "require"} <= set(entry):
            raise ModelError("every constraint needs id, before and require")
        for variable in entry["require"]:
            if variable not in initial:
                raise ModelError(f"constraint '{entry['id']}' requires unknown state '{variable}'")
        constraints.append(
            Constraint(
                str(entry["id"]),
                str(entry["before"]),
                dict(entry["require"]),
                str(entry.get("description", "")),
            )
        )
    return SimulationModel(initial, tuple(effects), tuple(constraints))


def load_model_file(path: Path | str) -> SimulationModel:
    try:
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ModelError(f"cannot read simulation model {path}: {error}") from error
    return parse_model(document)


def initial_state(
    model: SimulationModel, telemetry: Telemetry | None, now: datetime
) -> dict[str, Any]:
    """Starting values of the modeled state, from the latest telemetry."""
    state: dict[str, Any] = {}
    for name, source in model.initial.items():
        if "value" in source:
            state[name] = source["value"]
        elif telemetry is None:
            state[name] = UNKNOWN
        elif "channel_active" in source:
            sample = telemetry.get(source["channel_active"])
            window = float(source.get("within_seconds", 3))
            state[name] = (
                sample is not None and (now - sample.received_at).total_seconds() <= window
            )
        else:
            sample = telemetry.get(source["channel"])
            state[name] = sample.value if sample is not None else UNKNOWN
    return state


def _matches(call: CommandCall, pattern: str) -> bool:
    return call.name is not None and fnmatchcase(call.name, pattern)


def simulate(
    model: SimulationModel, sequence: SequenceModel, telemetry: Telemetry | None, now: datetime
) -> SimulationResult:
    """Predict the modeled state through the sequence and check the constraints."""
    start = initial_state(model, telemetry, now)
    modeled = [
        call
        for call in sequence.commands
        if any(_matches(call, e.command) for e in model.effects)
        or any(_matches(call, c.before) for c in model.constraints)
    ]
    if not modeled:
        return SimulationResult(start, (), ())

    if sequence.has_loop:
        return SimulationResult(start, (), (), "the sequence contains a loop")
    if sequence.telemetry_reads:
        channels = ", ".join(
            sorted({r.channel or str(r.channel_id) for r in sequence.telemetry_reads})
        )
        return SimulationResult(start, (), (), f"the sequence branches on telemetry ({channels})")

    state = dict(start)
    steps: list[Step] = []
    violations: list[SimulationViolation] = []
    for call in modeled:
        assert call.name is not None
        for constraint in model.constraints:
            if not _matches(call, constraint.before):
                continue
            for variable, required in constraint.require.items():
                actual = state[variable]
                if actual == UNKNOWN:
                    message = (
                        f"{variable} is not known when {call.name} is sent, "
                        f"but it must be {_show(required)}"
                    )
                elif actual != required:
                    message = (
                        f"{variable} is predicted to be {_show(actual)} when {call.name} is sent, "
                        f"but it must be {_show(required)}"
                    )
                else:
                    continue
                violations.append(
                    SimulationViolation(constraint.id, message, call.index, call.name)
                )

        for effect in model.effects:
            if not _matches(call, effect.command):
                continue
            for variable, value in effect.set.items():
                state[variable] = _resolve(value, call)
            for variable in effect.toggle:
                current = state[variable]
                state[variable] = (not current) if isinstance(current, bool) else UNKNOWN
        steps.append(Step(call.index, call.name, dict(state)))

    return SimulationResult(start, tuple(steps), tuple(violations))


def _resolve(value: Any, call: CommandCall) -> Any:
    """A literal, or the value of a command argument when written as "$Argument"."""
    if isinstance(value, str) and value.startswith("$"):
        if call.args is None or value[1:] not in call.args:
            return UNKNOWN
        return call.args[value[1:]]
    return value


def _show(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)
