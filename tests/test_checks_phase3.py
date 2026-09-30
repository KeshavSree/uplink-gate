"""The testbed_equiv and flight_rules checks, and recording testbed runs from events."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from gate.compile import compile_sequence
from gate.engine import evaluate
from gate.registry import Compile, Dictionary, Registry, TestbedRun
from gate.results import CheckResult, CheckStatus, GateDecision, Reason, Result
from gate.rules import load_rules_file
from gate.telemetry import EventReading
from gate.testbed import TestbedRecorder
from tests.conftest import NOW, SEQUENCES, VERSION_A, VERSION_B

ALL = ["build_match", "testbed_equiv", "flight_rules"]
DEMO_RULES = SEQUENCES.parent / "rules" / "demo_rules.yaml"


def by_name(decision: GateDecision) -> dict[str, CheckResult]:
    return {c.check: c for c in decision.checks}


def observe(registry: Registry, vehicle: str, version: str, kind: str | None = None) -> None:
    registry.upsert_vehicle(vehicle, kind=kind)
    registry.set_vehicle_state(vehicle, version, observed_at=NOW - timedelta(seconds=1))


def add_run(
    registry: Registry,
    record: Compile,
    dictionary_hash: str,
    result: str = "SUCCESS",
    vehicle: str = "testbed",
) -> TestbedRun:
    return registry.add_testbed_run(
        TestbedRun(
            bytecode_sha256=record.bytecode_sha256,
            dictionary_hash=dictionary_hash,
            vehicle_id=vehicle,
            result=result,
            started_at=NOW,
            finished_at=NOW,
            evidence={},
        )
    )


@pytest.fixture
def fleet(registry: Registry, dict_a: Dictionary, dict_b: Dictionary) -> Registry:
    observe(registry, "testbed", VERSION_A, kind="testbed")
    observe(registry, "sc1", VERSION_A)
    return registry


# --- testbed_equiv ------------------------------------------------------------------------


def test_blocks_without_a_testbed_run(fleet: Registry, compiled_a: Compile) -> None:
    decision = evaluate(fleet, compiled_a.bytecode_path, "sc1", checks=ALL, now=NOW)
    checks = by_name(decision)
    assert decision.result is Result.BLOCK
    assert checks["build_match"].status is CheckStatus.PASS
    assert checks["testbed_equiv"].reason is Reason.NO_TESTBED_RUN


def test_passes_after_a_successful_run_with_the_same_dictionary(
    fleet: Registry, compiled_a: Compile, dict_a: Dictionary
) -> None:
    run = add_run(fleet, compiled_a, dict_a.hash)
    result = by_name(evaluate(fleet, compiled_a.bytecode_path, "sc1", checks=ALL, now=NOW))[
        "testbed_equiv"
    ]
    assert result.status is CheckStatus.PASS
    assert result.details["testbed_run_id"] == run.id


def test_a_run_with_another_dictionary_does_not_count(
    fleet: Registry, compiled_a: Compile, dict_b: Dictionary
) -> None:
    add_run(fleet, compiled_a, dict_b.hash)
    result = by_name(evaluate(fleet, compiled_a.bytecode_path, "sc1", checks=ALL, now=NOW))
    assert result["testbed_equiv"].reason is Reason.NO_TESTBED_RUN


def test_a_run_of_other_bytecode_does_not_count(
    fleet: Registry, compiled_a: Compile, dict_a: Dictionary, tmp_path: Path
) -> None:
    other = compile_sequence(
        fleet, SEQUENCES / "signal_sweep.fpy", dict_a.hash, output_path=tmp_path / "sweep.bin"
    )
    add_run(fleet, other, dict_a.hash)
    result = by_name(evaluate(fleet, compiled_a.bytecode_path, "sc1", checks=ALL, now=NOW))
    assert result["testbed_equiv"].reason is Reason.NO_TESTBED_RUN


def test_a_run_on_a_spacecraft_is_not_testbed_evidence(
    fleet: Registry, compiled_a: Compile, dict_a: Dictionary
) -> None:
    observe(fleet, "sc2", VERSION_A)
    add_run(fleet, compiled_a, dict_a.hash, vehicle="sc2")
    result = by_name(evaluate(fleet, compiled_a.bytecode_path, "sc1", checks=ALL, now=NOW))
    assert result["testbed_equiv"].reason is Reason.NO_TESTBED_RUN


def test_the_latest_run_decides(fleet: Registry, compiled_a: Compile, dict_a: Dictionary) -> None:
    add_run(fleet, compiled_a, dict_a.hash, "SUCCESS")
    add_run(fleet, compiled_a, dict_a.hash, "FAIL")
    result = by_name(evaluate(fleet, compiled_a.bytecode_path, "sc1", checks=ALL, now=NOW))
    assert result["testbed_equiv"].reason is Reason.TESTBED_RUN_FAILED
    add_run(fleet, compiled_a, dict_a.hash, "SUCCESS")
    result = by_name(evaluate(fleet, compiled_a.bytecode_path, "sc1", checks=ALL, now=NOW))
    assert result["testbed_equiv"].status is CheckStatus.PASS


def test_testbed_evidence_is_lost_when_the_vehicle_changes_build(
    fleet: Registry, compiled_a: Compile, dict_a: Dictionary
) -> None:
    add_run(fleet, compiled_a, dict_a.hash)
    observe(fleet, "sc1", VERSION_B)
    checks = by_name(evaluate(fleet, compiled_a.bytecode_path, "sc1", checks=ALL, now=NOW))
    assert checks["build_match"].reason is Reason.DICTIONARY_MISMATCH
    assert checks["testbed_equiv"].reason is Reason.NO_TESTBED_RUN


def test_skipped_when_the_target_is_a_testbed(fleet: Registry, compiled_a: Compile) -> None:
    decision = evaluate(fleet, compiled_a.bytecode_path, "testbed", checks=ALL, now=NOW)
    assert by_name(decision)["testbed_equiv"].status is CheckStatus.SKIPPED
    assert by_name(decision)["testbed_equiv"].reason is Reason.TARGET_IS_TESTBED
    assert decision.result is Result.PASS


def test_blocks_when_the_running_dictionary_is_unknown(
    fleet: Registry, compiled_a: Compile
) -> None:
    observe(fleet, "sc1", "gate-demo-9.9.9")
    checks = by_name(evaluate(fleet, compiled_a.bytecode_path, "sc1", checks=ALL, now=NOW))
    assert checks["build_match"].reason is Reason.UNKNOWN_BUILD
    assert checks["testbed_equiv"].reason is Reason.RUNNING_DICTIONARY_UNRESOLVED
    assert checks["flight_rules"].reason is Reason.RUNNING_DICTIONARY_UNRESOLVED


# --- flight_rules -------------------------------------------------------------------------


def rules_check(registry: Registry, bytecode: str | Path, vehicle: str = "sc1") -> CheckResult:
    decision = evaluate(
        registry, bytecode, vehicle, checks=["build_match", "flight_rules"], now=NOW
    )
    return by_name(decision)["flight_rules"]


def test_skipped_when_no_rules_are_loaded(fleet: Registry, compiled_a: Compile) -> None:
    result = rules_check(fleet, compiled_a.bytecode_path)
    assert result.status is CheckStatus.SKIPPED and result.reason is Reason.NO_FLIGHT_RULES


def test_passes_a_sequence_that_keeps_the_rules(fleet: Registry, compiled_a: Compile) -> None:
    fleet.load_flight_rules(load_rules_file(DEMO_RULES))
    result = rules_check(fleet, compiled_a.bytecode_path)
    assert result.status is CheckStatus.PASS
    assert set(result.details["rules_checked"]) == {
        "sg-frequency-range",
        "sg-retune-spacing",
        "no-parameter-save",
    }
    assert result.details["commands"] == ["Ref.SG1.Settings", "Ref.SG1.Toggle"]


def test_blocks_a_sequence_that_breaks_rules(
    fleet: Registry, dict_a: Dictionary, tmp_path: Path
) -> None:
    fleet.load_flight_rules(load_rules_file(DEMO_RULES))
    record = compile_sequence(
        fleet, SEQUENCES / "signal_too_fast.fpy", dict_a.hash, output_path=tmp_path / "fast.bin"
    )
    result = rules_check(fleet, record.bytecode_path)
    assert result.reason is Reason.FLIGHT_RULE_VIOLATION
    assert {v["rule_id"] for v in result.details["violations"]} == {
        "sg-frequency-range",
        "sg-retune-spacing",
    }


def test_a_rule_keyed_to_another_dictionary_does_not_apply(
    fleet: Registry, dict_a: Dictionary, dict_b: Dictionary, tmp_path: Path
) -> None:
    rules = load_rules_file(DEMO_RULES)
    keyed = [
        type(r)(r.id, r.type, r.params, r.description, dictionary_hash=dict_b.hash) for r in rules
    ]
    fleet.load_flight_rules(keyed)
    record = compile_sequence(
        fleet, SEQUENCES / "signal_too_fast.fpy", dict_a.hash, output_path=tmp_path / "fast.bin"
    )
    assert rules_check(fleet, record.bytecode_path).reason is Reason.NO_FLIGHT_RULES

    fleet.load_flight_rules(
        [type(r)(r.id, r.type, r.params, r.description, dictionary_hash=dict_a.hash) for r in rules]
    )
    assert rules_check(fleet, record.bytecode_path).reason is Reason.FLIGHT_RULE_VIOLATION


def test_mismatched_bytecode_does_not_decode_with_the_running_dictionary(
    fleet: Registry, compiled_a: Compile
) -> None:
    """A second, independent sign of a build mismatch: the arguments do not fit."""
    observe(fleet, "sc1", VERSION_B)
    result = rules_check(fleet, compiled_a.bytecode_path)
    assert result.reason is Reason.BYTECODE_UNREADABLE
    assert result.details["undecodable"][0]["command"] == "Ref.SG1.Settings"


def test_file_that_is_not_bytecode_blocks(fleet: Registry, tmp_path: Path) -> None:
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"\x00" * 40)
    assert rules_check(fleet, junk).reason is Reason.BYTECODE_UNREADABLE


# --- recording testbed runs from sequencer events -----------------------------------------


def event(vehicle: str, name: str, severity: str, message: str) -> EventReading:
    return EventReading(vehicle, f"Ref.fpySeq.{name}", severity, message, NOW)


def test_recorder_writes_a_success_run_and_the_gate_then_passes(
    fleet: Registry, compiled_a: Compile, dict_a: Dictionary
) -> None:
    fleet.set_resolved_dictionary("testbed", dict_a.hash)
    published: list[tuple[str, dict]] = []
    recorder = TestbedRecorder(
        fleet, publish=lambda kind, payload: published.append((kind, payload))
    )
    remote = f"/tmp/uplink/{compiled_a.bytecode_sha256[:16]}.bin"

    assert (
        recorder.on_event(
            event("testbed", "LogActivityHi", "ACTIVITY_HI", f"Sequence {remote}: hi")
        )
        is None
    )
    run = recorder.on_event(
        event("testbed", "SequenceDone", "ACTIVITY_HI", f"Completed sequence file {remote}")
    )
    assert run is not None
    assert (run.result, run.bytecode_sha256, run.dictionary_hash, run.vehicle_id) == (
        "SUCCESS",
        compiled_a.bytecode_sha256,
        dict_a.hash,
        "testbed",
    )
    assert published[0][0] == "testbed.run"

    decision = evaluate(fleet, compiled_a.bytecode_path, "sc1", checks=ALL, now=NOW)
    assert decision.result is Result.PASS


def test_recorder_writes_a_failed_run(
    fleet: Registry, compiled_a: Compile, dict_a: Dictionary
) -> None:
    fleet.set_resolved_dictionary("testbed", dict_a.hash)
    remote = f"/tmp/uplink/{compiled_a.bytecode_sha256[:16]}.bin"
    run = TestbedRecorder(fleet).on_event(
        event(
            "testbed",
            "SequenceExitedWithError",
            "WARNING_HI",
            f"Sequence {remote} exited with error code 17",
        )
    )
    assert run is not None and run.result == "FAIL"
    result = by_name(evaluate(fleet, compiled_a.bytecode_path, "sc1", checks=ALL, now=NOW))
    assert result["testbed_equiv"].reason is Reason.TESTBED_RUN_FAILED


def test_recorder_ignores_what_it_cannot_tie_to_a_compile(
    fleet: Registry, compiled_a: Compile, dict_a: Dictionary
) -> None:
    fleet.set_resolved_dictionary("testbed", dict_a.hash)
    fleet.set_resolved_dictionary("sc1", dict_a.hash)
    recorder = TestbedRecorder(fleet)
    remote = f"/tmp/uplink/{compiled_a.bytecode_sha256[:16]}.bin"
    done = "Completed sequence file "
    # A run on a spacecraft, a file not named by hash, an unknown hash, another component.
    assert recorder.on_event(event("sc1", "SequenceDone", "ACTIVITY_HI", done + remote)) is None
    assert (
        recorder.on_event(event("testbed", "SequenceDone", "ACTIVITY_HI", done + "/seq/a.bin"))
        is None
    )
    assert (
        recorder.on_event(
            event(
                "testbed", "SequenceDone", "ACTIVITY_HI", done + "/tmp/uplink/0123456789abcdef.bin"
            )
        )
        is None
    )
    other = EventReading(
        "testbed", "Ref.cmdSeq.CS_SequenceComplete", "ACTIVITY_HI", done + remote, NOW
    )
    assert recorder.on_event(other) is None
    assert fleet.list_testbed_runs() == []
