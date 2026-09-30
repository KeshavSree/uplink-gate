"""The build_match check and the rules for resolving what a vehicle is running."""

from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from gate.compile import compile_sequence, sidecar_path
from gate.config import GateConfig
from gate.engine import evaluate
from gate.registry import Compile, Dictionary, Registry
from gate.results import CheckStatus, GateDecision, Reason, Result
from tests.conftest import DICT_B_STALE, NOW, VERSION_A, VERSION_B


def check(registry: Registry, bytecode: str | Path, vehicle: str = "sc1", **kwargs) -> GateDecision:
    return evaluate(registry, bytecode, vehicle, checks=["build_match"], now=NOW, **kwargs)


def observe(
    registry: Registry, version: str, vehicle: str = "sc1", age_s: float = 5, **kwargs
) -> None:
    registry.upsert_vehicle(vehicle)
    registry.set_vehicle_state(
        vehicle, version, observed_at=NOW - timedelta(seconds=age_s), **kwargs
    )


def only(decision: GateDecision):
    assert len(decision.checks) == 1
    return decision.checks[0]


def test_pass_when_vehicle_runs_the_dictionary_the_bytecode_was_compiled_with(
    registry: Registry, compiled_a: Compile, dict_a: Dictionary
) -> None:
    observe(registry, VERSION_A)
    decision = check(registry, compiled_a.bytecode_path)
    assert decision.result is Result.PASS
    assert only(decision).reason is Reason.OK
    assert decision.running_dictionary_hash == dict_a.hash
    assert decision.compile_id == compiled_a.id
    assert decision.id is not None
    assert registry.get_decision(decision.id) == decision


def test_block_wrong_dictionary(
    registry: Registry, compiled_a: Compile, dict_a: Dictionary, dict_b: Dictionary
) -> None:
    observe(registry, VERSION_B)
    decision = check(registry, compiled_a.bytecode_path)
    result = only(decision)
    assert decision.result is Result.BLOCK
    assert result.reason is Reason.DICTIONARY_MISMATCH
    assert result.details["running_dictionary_hash"] == dict_b.hash
    assert result.details["compiled_with"] == [
        {"dictionary_hash": dict_a.hash, "project_version": VERSION_A}
    ]
    assert decision.compile_id is None


def test_block_unknown_version(registry: Registry, compiled_a: Compile) -> None:
    observe(registry, "gate-demo-9.9.9")
    assert only(check(registry, compiled_a.bytecode_path)).reason is Reason.UNKNOWN_BUILD


def test_block_ambiguous_version(registry: Registry, compiled_a: Compile) -> None:
    # A real dictionary with different content but the same project version label.
    registry.add_dictionary(DICT_B_STALE)
    observe(registry, VERSION_A)
    result = only(check(registry, compiled_a.bytecode_path))
    assert result.reason is Reason.AMBIGUOUS_BUILD


def test_dictionary_id_disambiguates_an_ambiguous_version(
    registry: Registry, compiled_a: Compile, dict_a: Dictionary
) -> None:
    stale = registry.add_dictionary(DICT_B_STALE)
    observe(registry, VERSION_A, dictionary_id=f"dict-sha256:{dict_a.hash[:16]}")
    decision = check(registry, compiled_a.bytecode_path)
    assert decision.result is Result.PASS
    assert only(decision).details["identified_by"] == "content"

    observe(registry, VERSION_A, dictionary_id=f"dict-sha256:{stale.hash[:16]}")
    blocked = check(registry, compiled_a.bytecode_path)
    assert only(blocked).reason is Reason.DICTIONARY_MISMATCH
    assert blocked.running_dictionary_hash == stale.hash


def test_block_dictionary_id_unknown_to_registry(registry: Registry, compiled_a: Compile) -> None:
    observe(registry, VERSION_A, dictionary_id="dict-sha256:0123456789abcdef")
    assert only(check(registry, compiled_a.bytecode_path)).reason is Reason.UNKNOWN_BUILD


def test_block_when_label_and_dictionary_id_disagree(
    registry: Registry, compiled_a: Compile, dict_b: Dictionary
) -> None:
    observe(registry, VERSION_A, dictionary_id=f"dict-sha256:{dict_b.hash[:16]}")
    result = only(check(registry, compiled_a.bytecode_path))
    assert result.reason is Reason.BUILD_IDENTITY_CONFLICT


def test_placeholder_dictionary_id_falls_back_to_the_label(
    registry: Registry, compiled_a: Compile
) -> None:
    observe(registry, VERSION_A, dictionary_id="no_ver")
    decision = check(registry, compiled_a.bytecode_path)
    assert decision.result is Result.PASS
    assert only(decision).details["identified_by"] == "label"


@pytest.mark.parametrize("suffix", ["-dirty", "-broken"])
def test_block_dirty_version(registry: Registry, compiled_a: Compile, suffix: str) -> None:
    observe(registry, VERSION_A + suffix)
    assert only(check(registry, compiled_a.bytecode_path)).reason is Reason.DIRTY_BUILD


def test_dirty_version_is_configurable(
    registry: Registry, config: GateConfig, sequence: Path, tmp_path: Path
) -> None:
    dirty = tmp_path / "dirty.json"
    dirty.write_text(
        DICT_B_STALE.read_text().replace('"gate-demo-1.0.0"', '"gate-demo-1.0.0-dirty"')
    )
    dictionary = registry.add_dictionary(dirty)
    assert dictionary.project_version == "gate-demo-1.0.0-dirty"
    record = compile_sequence(registry, sequence, dictionary.hash)
    observe(registry, "gate-demo-1.0.0-dirty")

    assert only(check(registry, record.bytecode_path)).reason is Reason.DIRTY_BUILD
    allowed = check(registry, record.bytecode_path, config=config.with_(allow_dirty=True))
    assert allowed.result is Result.PASS


def test_block_stale_state(registry: Registry, compiled_a: Compile) -> None:
    observe(registry, VERSION_A, age_s=61)
    result = only(check(registry, compiled_a.bytecode_path))
    assert result.reason is Reason.STALE_BUILD_IDENTITY
    assert result.details["observed_project_version"] == VERSION_A


def test_state_at_the_staleness_limit_still_passes(registry: Registry, compiled_a: Compile) -> None:
    observe(registry, VERSION_A, age_s=60)
    assert check(registry, compiled_a.bytecode_path).result is Result.PASS


def test_staleness_limit_is_configurable(
    registry: Registry, config: GateConfig, compiled_a: Compile
) -> None:
    observe(registry, VERSION_A, age_s=30)
    strict = config.with_(max_state_age_s=10)
    result = only(check(registry, compiled_a.bytecode_path, config=strict))
    assert result.reason is Reason.STALE_BUILD_IDENTITY


def test_block_no_state(registry: Registry, compiled_a: Compile) -> None:
    registry.upsert_vehicle("sc1")
    assert only(check(registry, compiled_a.bytecode_path)).reason is Reason.NO_BUILD_IDENTITY


def test_block_unknown_vehicle(registry: Registry, compiled_a: Compile) -> None:
    assert only(check(registry, compiled_a.bytecode_path)).reason is Reason.UNKNOWN_VEHICLE


def test_block_bytecode_with_no_sidecar_and_no_registry_record(
    registry: Registry, compiled_a: Compile, tmp_path: Path
) -> None:
    """Bytecode produced by running fprime-fpyc directly, outside the gate."""
    from gate.compile import run_compiler
    from tests.conftest import DICT_B

    outside = run_compiler(compiled_a.sequence_path, DICT_B, tmp_path / "outside.bin")
    observe(registry, VERSION_A)
    result = only(check(registry, outside.bytecode_path))
    assert result.reason is Reason.NO_COMPILE_RECORD
    assert result.status is CheckStatus.BLOCK


def test_registry_record_is_enough_without_the_sidecar(
    registry: Registry, compiled_a: Compile
) -> None:
    sidecar_path(compiled_a.bytecode_path).unlink()
    observe(registry, VERSION_A)
    assert check(registry, compiled_a.bytecode_path).result is Result.PASS


def test_sidecar_is_enough_without_the_registry_record(
    config: GateConfig, compiled_a: Compile, dict_a: Dictionary, tmp_path: Path
) -> None:
    """The sidecar makes the compile record portable to a registry that never saw the compile."""
    other = Registry(config.with_(home=tmp_path / "other-home"))
    other.add_dictionary(dict_a.path)
    observe(other, VERSION_A)
    assert other.compiles_for_bytecode(compiled_a.bytecode_sha256) == []
    assert check(other, compiled_a.bytecode_path).result is Result.PASS


def test_block_bytecode_modified_after_compile(registry: Registry, compiled_a: Compile) -> None:
    path = Path(compiled_a.bytecode_path)
    data = bytearray(path.read_bytes())
    data[-1] ^= 0xFF
    path.write_bytes(bytes(data))
    observe(registry, VERSION_A)
    assert only(check(registry, path)).reason is Reason.BYTECODE_MODIFIED


def test_block_sidecar_copied_onto_other_bytecode(
    registry: Registry, compiled_a: Compile, dict_b: Dictionary, sequence: Path, tmp_path: Path
) -> None:
    compiled_b = compile_sequence(registry, sequence, dict_b.hash, output_path=tmp_path / "b.bin")
    # Pretend B's bytecode was compiled for A by giving it A's sidecar.
    unknown = tmp_path / "relabelled.bin"
    shutil.copyfile(compiled_b.bytecode_path, unknown)
    shutil.copyfile(sidecar_path(compiled_a.bytecode_path), sidecar_path(unknown))
    observe(registry, VERSION_A)
    assert only(check(registry, unknown)).reason is Reason.BYTECODE_MODIFIED


def test_corrupt_sidecar_blocks(registry: Registry, compiled_a: Compile) -> None:
    sidecar_path(compiled_a.bytecode_path).write_text("{")
    observe(registry, VERSION_A)
    assert only(check(registry, compiled_a.bytecode_path)).reason is Reason.NO_COMPILE_RECORD


def test_decision_flips_when_the_vehicle_changes_build(
    registry: Registry, compiled_a: Compile, dict_b: Dictionary
) -> None:
    """The live-swap case: same bytecode, the vehicle is reflashed from build A to build B."""
    observe(registry, VERSION_A)
    assert check(registry, compiled_a.bytecode_path).result is Result.PASS
    observe(registry, VERSION_B)
    assert only(check(registry, compiled_a.bytecode_path)).reason is Reason.DICTIONARY_MISMATCH


def test_identical_bytecode_from_two_dictionaries_passes_on_either(
    registry: Registry, dict_a: Dictionary, dict_b: Dictionary, tmp_path: Path
) -> None:
    """A sequence that touches nothing that differs compiles to the same bytes for both."""
    noop = tmp_path / "noop.fpy"
    noop.write_text("CdhCore.cmdDisp.CMD_NO_OP()\n")
    for_a = compile_sequence(registry, noop, dict_a.hash, output_path=tmp_path / "noop_a.bin")
    for_b = compile_sequence(registry, noop, dict_b.hash, output_path=tmp_path / "noop_b.bin")
    assert for_a.bytecode_sha256 == for_b.bytecode_sha256

    observe(registry, VERSION_B)
    decision = check(registry, for_a.bytecode_path)
    assert decision.result is Result.PASS
    assert decision.compile_id == for_b.id


def test_record_false_does_not_store_the_decision(registry: Registry, compiled_a: Compile) -> None:
    observe(registry, VERSION_A)
    decision = check(registry, compiled_a.bytecode_path, record=False)
    assert decision.id is None
    assert registry.list_decisions() == []


def test_unknown_check_name_is_rejected(registry: Registry, compiled_a: Compile) -> None:
    with pytest.raises(ValueError, match="unknown check"):
        evaluate(registry, compiled_a.bytecode_path, "sc1", checks=["nope"], now=NOW)
