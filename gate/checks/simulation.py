"""simulation: starting from the vehicle's latest telemetry, does the sequence drive the
modeled state somewhere it must not go?

See gate.simulation for the model and its limits. Without a model the check is skipped.
"""

from __future__ import annotations

from gate.bytecode import BytecodeError, SequenceModel, load_model
from gate.checks import CheckContext, blocked, passed, skipped
from gate.results import CheckResult, Reason
from gate.simulation import ModelError, load_model_file, simulate

NAME = "simulation"


def simulation(ctx: CheckContext) -> CheckResult:
    model_path = ctx.config.simulation_model
    if model_path is None:
        return skipped(NAME, Reason.NOT_SIMULATED, "no simulation model is configured")
    try:
        model = load_model_file(model_path)
    except ModelError as error:
        return blocked(NAME, Reason.SIMULATION_UNPREDICTABLE, str(error))

    running = ctx.running_dictionary_hash
    if running is None:
        return blocked(
            NAME,
            Reason.RUNNING_DICTIONARY_UNRESOLVED,
            "the sequence is simulated as the vehicle's dictionary reads it, "
            "and that dictionary is not known",
        )

    sequence = ctx.scratch.get("sequence_model")
    if not isinstance(sequence, SequenceModel):
        dictionary = ctx.registry.get_dictionary(running)
        assert dictionary is not None
        try:
            sequence = load_model(ctx.bytecode_path, dictionary.path)
        except BytecodeError as error:
            return blocked(NAME, Reason.BYTECODE_UNREADABLE, str(error))

    result = simulate(model, sequence, ctx.telemetry, ctx.now)
    details = {
        "initial_state": result.initial_state,
        "final_state": result.final_state,
        "steps": [step.to_dict() for step in result.steps],
        "telemetry_available": ctx.telemetry is not None,
    }
    if result.unpredictable is not None:
        return blocked(
            NAME,
            Reason.SIMULATION_UNPREDICTABLE,
            f"cannot predict the modeled state: {result.unpredictable}",
            **details,
        )
    if result.violations:
        return blocked(
            NAME,
            Reason.SIMULATION_VIOLATION,
            result.violations[0].message,
            violations=[v.to_dict() for v in result.violations],
            **details,
        )
    if not result.steps:
        return passed(NAME, "the sequence sends no command the model covers", **details)
    final = ", ".join(
        f"{k}={str(v).lower() if isinstance(v, bool) else v}" for k, v in result.final_state.items()
    )
    return passed(NAME, f"predicted state after the sequence: {final}", **details)
