"""Predicting modeled state through a sequence, starting from telemetry."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from gate.bytecode import load_model
from gate.compile import compile_sequence, run_compiler
from gate.config import GateConfig
from gate.engine import evaluate
from gate.registry import Dictionary, Registry
from gate.results import CheckResult, CheckStatus, Reason
from gate.simulation import (
    UNKNOWN,
    ModelError,
    Sample,
    initial_state,
    load_model_file,
    parse_model,
    simulate,
)
from tests.conftest import DICT_A, NOW, SEQUENCES, VERSION_A

MODEL_FILE = SEQUENCES.parent / "rules" / "demo_model.yaml"
SETUP = "Ref.SG1.Settings(5, 2.5, 0.0, Ref.SignalType.SINE)\nRef.SG1.Toggle()\n"

RUNNING = {
    "Ref.SG1.Output": Sample(0.7, NOW - timedelta(seconds=1)),
    "Ref.SG1.Type": Sample("SQUARE", NOW - timedelta(seconds=1)),
}
STOPPED = {"Ref.SG1.Output": Sample(0.7, NOW - timedelta(seconds=30))}


def sequence_of(tmp_path: Path, source: str):
    path = tmp_path / "seq.fpy"
    path.write_text(source)
    return load_model(run_compiler(path, DICT_A, tmp_path / "seq.bin").bytecode_path, DICT_A)


@pytest.fixture
def model():
    return load_model_file(MODEL_FILE)


def test_initial_state_comes_from_telemetry(model) -> None:
    assert initial_state(model, RUNNING, NOW) == {"sg1_running": True, "sg1_type": "SQUARE"}
    assert initial_state(model, STOPPED, NOW) == {"sg1_running": False, "sg1_type": UNKNOWN}
    assert initial_state(model, {}, NOW) == {"sg1_running": False, "sg1_type": UNKNOWN}
    # No link at all: nothing can be assumed.
    assert initial_state(model, None, NOW) == {"sg1_running": UNKNOWN, "sg1_type": UNKNOWN}


def test_setup_is_predicted_from_a_stopped_generator(model, tmp_path: Path) -> None:
    result = simulate(model, sequence_of(tmp_path, SETUP), STOPPED, NOW)
    assert result.violations == ()
    assert [(s.command, s.state) for s in result.steps] == [
        ("Ref.SG1.Settings", {"sg1_running": False, "sg1_type": "SINE"}),
        ("Ref.SG1.Toggle", {"sg1_running": True, "sg1_type": "SINE"}),
    ]
    assert result.final_state == {"sg1_running": True, "sg1_type": "SINE"}


def test_the_same_sequence_violates_when_the_generator_already_runs(model, tmp_path: Path) -> None:
    result = simulate(model, sequence_of(tmp_path, SETUP), RUNNING, NOW)
    assert [v.constraint_id for v in result.violations] == ["sg1-settings-only-while-stopped"]
    assert "predicted to be true" in result.violations[0].message
    # and the toggle would switch it off, not on
    assert result.final_state["sg1_running"] is False


def test_state_changed_earlier_in_the_sequence_counts(model, tmp_path: Path) -> None:
    source = "Ref.SG1.Toggle()\nRef.SG1.Settings(5, 2.5, 0.0, Ref.SignalType.SINE)\n"
    assert len(simulate(model, sequence_of(tmp_path, source), STOPPED, NOW).violations) == 1
    assert simulate(model, sequence_of(tmp_path, source), RUNNING, NOW).violations == ()


def test_unknown_state_is_a_violation(model, tmp_path: Path) -> None:
    result = simulate(model, sequence_of(tmp_path, SETUP), None, NOW)
    assert len(result.violations) == 1 and "is not known" in result.violations[0].message
    assert result.final_state["sg1_running"] == UNKNOWN


def test_sequence_without_modeled_commands_has_no_steps(model, tmp_path: Path) -> None:
    result = simulate(model, sequence_of(tmp_path, "CdhCore.cmdDisp.CMD_NO_OP()\n"), RUNNING, NOW)
    assert result.steps == () and result.violations == () and result.unpredictable is None


def test_loops_and_telemetry_branches_are_unpredictable(model, tmp_path: Path) -> None:
    loop = sequence_of(tmp_path, "for i in 0..3:\n    Ref.SG1.Toggle()\n")
    assert "loop" in (simulate(model, loop, STOPPED, NOW).unpredictable or "")
    branch = sequence_of(tmp_path, "if Ref.SG1.Output > 0.5:\n    Ref.SG1.Toggle()\n")
    assert "Ref.SG1.Output" in (simulate(model, branch, STOPPED, NOW).unpredictable or "")


def test_effect_from_a_computed_argument_is_unknown(model, tmp_path: Path) -> None:
    source = "f: U32 = 4\nRef.SG1.Settings(f, 1.0, 0.0, Ref.SignalType.SINE)\n"
    result = simulate(model, sequence_of(tmp_path, source), STOPPED, NOW)
    assert result.final_state["sg1_type"] == UNKNOWN


@pytest.mark.parametrize(
    "document",
    [
        None,
        {"simulation": {}},
        {"simulation": {"state": {"a": {}}}},
        {"simulation": {"state": {"a": {"initial": {"value": 1}}}, "effects": [{"toggle": "a"}]}},
        {
            "simulation": {
                "state": {"a": {"initial": {"value": 1}}},
                "effects": [{"command": "X", "toggle": "b"}],
            }
        },
        {
            "simulation": {
                "state": {"a": {"initial": {"value": 1}}},
                "constraints": [{"id": "c", "before": "X"}],
            }
        },
        {
            "simulation": {
                "state": {"a": {"initial": {"value": 1}}},
                "constraints": [{"id": "c", "before": "X", "require": {"b": 1}}],
            }
        },
    ],
)
def test_malformed_models_are_rejected(document: object) -> None:
    with pytest.raises(ModelError):
        parse_model(document)


def test_model_with_a_fixed_initial_value() -> None:
    model = parse_model({"simulation": {"state": {"mode": {"initial": {"value": "SAFE"}}}}})
    assert initial_state(model, None, NOW) == {"mode": "SAFE"}


# --- the check ----------------------------------------------------------------------------


def sim_check(registry: Registry, config: GateConfig, bytecode: str, telemetry) -> CheckResult:
    decision = evaluate(
        registry,
        bytecode,
        "sc1",
        config=config,
        checks=["build_match", "simulation"],
        now=NOW,
        telemetry=telemetry,
    )
    return {c.check: c for c in decision.checks}["simulation"]


@pytest.fixture
def ready(registry: Registry, dict_a: Dictionary, sequence: Path) -> str:
    registry.upsert_vehicle("sc1")
    registry.set_vehicle_state("sc1", VERSION_A, observed_at=NOW)
    return compile_sequence(registry, sequence, dict_a.hash).bytecode_path


def test_check_is_skipped_without_a_model(
    registry: Registry, config: GateConfig, ready: str
) -> None:
    result = sim_check(registry, config, ready, STOPPED)
    assert result.status is CheckStatus.SKIPPED and result.reason is Reason.NOT_SIMULATED


def test_check_passes_and_reports_the_predicted_state(
    registry: Registry, config: GateConfig, ready: str
) -> None:
    result = sim_check(registry, config.with_(simulation_model=MODEL_FILE), ready, STOPPED)
    assert result.status is CheckStatus.PASS
    assert result.details["initial_state"]["sg1_running"] is False
    assert result.details["final_state"] == {"sg1_running": True, "sg1_type": "SINE"}
    assert "sg1_running=true" in result.message


def test_check_blocks_on_live_state(registry: Registry, config: GateConfig, ready: str) -> None:
    result = sim_check(registry, config.with_(simulation_model=MODEL_FILE), ready, RUNNING)
    assert result.reason is Reason.SIMULATION_VIOLATION
    assert result.details["violations"][0]["constraint_id"] == "sg1-settings-only-while-stopped"


def test_check_blocks_without_telemetry(registry: Registry, config: GateConfig, ready: str) -> None:
    result = sim_check(registry, config.with_(simulation_model=MODEL_FILE), ready, None)
    assert result.reason is Reason.SIMULATION_VIOLATION
    assert result.details["telemetry_available"] is False


def test_check_blocks_an_unpredictable_sequence(
    registry: Registry, config: GateConfig, dict_a: Dictionary, tmp_path: Path, ready: str
) -> None:
    loop = tmp_path / "loop.fpy"
    loop.write_text("for i in 0..3:\n    Ref.SG1.Toggle()\n")
    record = compile_sequence(registry, loop, dict_a.hash)
    result = sim_check(
        registry, config.with_(simulation_model=MODEL_FILE), record.bytecode_path, STOPPED
    )
    assert result.reason is Reason.SIMULATION_UNPREDICTABLE


def test_check_blocks_on_a_broken_model_or_unknown_build(
    registry: Registry, config: GateConfig, ready: str, tmp_path: Path
) -> None:
    broken = tmp_path / "model.yaml"
    broken.write_text("simulation: {}")
    result = sim_check(registry, config.with_(simulation_model=broken), ready, STOPPED)
    assert result.reason is Reason.SIMULATION_UNPREDICTABLE

    registry.set_vehicle_state("sc1", "gate-demo-9.9.9", observed_at=NOW)
    result = sim_check(registry, config.with_(simulation_model=MODEL_FILE), ready, STOPPED)
    assert result.reason is Reason.RUNNING_DICTIONARY_UNRESOLVED
