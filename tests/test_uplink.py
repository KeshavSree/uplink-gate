"""The uplink adapter must be impossible to get bytes through without a current PASS."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from gate.config import GateConfig
from gate.engine import evaluate
from gate.registry import Compile, Dictionary, Registry, utcnow
from gate.results import Reason, Result
from gate.uplink import UplinkAdapter, UplinkRefused
from tests.conftest import VERSION_A, VERSION_B
from tests.fakes import FakeComms


@pytest.fixture
def comms(registry: Registry, dict_a: Dictionary) -> FakeComms:
    registry.upsert_vehicle("sc1")
    registry.set_vehicle_state("sc1", VERSION_A)
    comms = FakeComms(registry)
    comms.identity["sc1"] = (VERSION_A, f"dict-sha256:{dict_a.hash[:16]}")
    return comms


@pytest.fixture
def adapter(registry: Registry, comms: FakeComms) -> UplinkAdapter:
    return UplinkAdapter(registry, comms, file_timeout_s=0.1, run_timeout_s=0.1)


def test_uplink_after_pass_sends_file_and_runs_it(
    registry: Registry, adapter: UplinkAdapter, comms: FakeComms, compiled_a: Compile
) -> None:
    decision = evaluate(registry, compiled_a.bytecode_path, "sc1")
    assert decision.passed
    result = adapter.uplink(compiled_a.bytecode_path, "sc1")

    remote = f"/tmp/uplink/{compiled_a.bytecode_sha256[:16]}.bin"
    assert result.ok and result.remote_path == remote
    assert comms.sent_files == [("sc1", Path(compiled_a.bytecode_path), remote)]
    assert comms.sent_commands == [("sc1", "Ref.fpySeq.RUN", [remote, "BLOCK"])]
    assert [e.name.rsplit(".", 1)[-1] for e in result.events] == ["FileReceived", "SequenceDone"]

    # The adapter re-ran the gate right before sending and tied the uplink to that decision.
    assert result.decision_id != decision.id
    uplinks = registry.list_uplinks("sc1")
    assert len(uplinks) == 1 and uplinks[0].ok and uplinks[0].decision_id == result.decision_id


def test_refuses_bytecode_that_was_never_checked(
    adapter: UplinkAdapter, comms: FakeComms, compiled_a: Compile
) -> None:
    with pytest.raises(UplinkRefused) as refused:
        adapter.uplink(compiled_a.bytecode_path, "sc1")
    assert refused.value.reason is Reason.NO_PASS_DECISION
    assert comms.sent_files == [] and comms.sent_commands == []


def test_refuses_after_a_block_decision(
    registry: Registry,
    adapter: UplinkAdapter,
    comms: FakeComms,
    compiled_a: Compile,
    dict_b: Dictionary,
) -> None:
    registry.set_vehicle_state("sc1", VERSION_B)
    assert not evaluate(registry, compiled_a.bytecode_path, "sc1").passed
    with pytest.raises(UplinkRefused) as refused:
        adapter.uplink(compiled_a.bytecode_path, "sc1")
    assert refused.value.reason is Reason.NO_PASS_DECISION
    assert comms.sent_files == []


def test_refuses_a_stale_pass_decision(
    registry: Registry,
    adapter: UplinkAdapter,
    comms: FakeComms,
    compiled_a: Compile,
    config: GateConfig,
) -> None:
    old = utcnow() - timedelta(seconds=config.max_decision_age_s + 1)
    registry.set_vehicle_state("sc1", VERSION_A, observed_at=old)
    assert evaluate(registry, compiled_a.bytecode_path, "sc1", now=old).passed
    with pytest.raises(UplinkRefused) as refused:
        adapter.uplink(compiled_a.bytecode_path, "sc1")
    assert refused.value.reason is Reason.STALE_DECISION
    assert comms.sent_files == []


def test_refuses_a_decision_for_another_vehicle_or_other_bytecode(
    registry: Registry, adapter: UplinkAdapter, comms: FakeComms, compiled_a: Compile
) -> None:
    registry.upsert_vehicle("sc2")
    registry.set_vehicle_state("sc2", VERSION_A)
    comms.identity["sc2"] = comms.identity["sc1"]
    for_sc2 = evaluate(registry, compiled_a.bytecode_path, "sc2")
    assert for_sc2.passed
    with pytest.raises(UplinkRefused) as refused:
        adapter.uplink(compiled_a.bytecode_path, "sc1", decision_id=for_sc2.id)
    assert refused.value.reason is Reason.NO_PASS_DECISION
    with pytest.raises(UplinkRefused):
        adapter.uplink(compiled_a.bytecode_path, "sc1", decision_id=999)
    assert comms.sent_files == []


def test_recheck_catches_a_reflash_between_decision_and_uplink(
    registry: Registry,
    adapter: UplinkAdapter,
    comms: FakeComms,
    compiled_a: Compile,
    dict_b: Dictionary,
) -> None:
    """PASS was decided, then the vehicle changed build. The re-check must block the send."""
    assert evaluate(registry, compiled_a.bytecode_path, "sc1").passed
    comms.identity["sc1"] = (VERSION_B, f"dict-sha256:{dict_b.hash[:16]}")

    with pytest.raises(UplinkRefused) as refused:
        adapter.uplink(compiled_a.bytecode_path, "sc1")
    assert refused.value.reason is Reason.DICTIONARY_MISMATCH
    assert refused.value.decision is not None
    assert refused.value.decision.result is Result.BLOCK
    assert comms.sent_files == [] and comms.sent_commands == []
    uplinks = registry.list_uplinks("sc1")
    assert len(uplinks) == 1 and not uplinks[0].ok


def test_refuses_when_the_vehicle_does_not_confirm_its_identity(
    registry: Registry, adapter: UplinkAdapter, comms: FakeComms, compiled_a: Compile
) -> None:
    assert evaluate(registry, compiled_a.bytecode_path, "sc1").passed
    comms.answers_identity = False
    with pytest.raises(UplinkRefused) as refused:
        adapter.uplink(compiled_a.bytecode_path, "sc1")
    assert refused.value.reason is Reason.STALE_BUILD_IDENTITY
    assert comms.sent_files == []


def test_refuses_when_the_link_is_down(
    registry: Registry, adapter: UplinkAdapter, comms: FakeComms, compiled_a: Compile
) -> None:
    assert evaluate(registry, compiled_a.bytecode_path, "sc1").passed
    comms.link_up = False
    with pytest.raises(UplinkRefused) as refused:
        adapter.uplink(compiled_a.bytecode_path, "sc1")
    assert refused.value.reason is Reason.UPLINK_FAILED


def test_reports_failure_when_the_file_is_not_confirmed(
    registry: Registry, adapter: UplinkAdapter, comms: FakeComms, compiled_a: Compile
) -> None:
    assert evaluate(registry, compiled_a.bytecode_path, "sc1").passed
    comms.confirms_file = False
    result = adapter.uplink(compiled_a.bytecode_path, "sc1")
    assert not result.ok and "did not confirm" in result.detail
    assert comms.sent_commands == []
    assert not registry.list_uplinks("sc1")[0].ok


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("error", "SequenceExitedWithError"), ("silent", "no completion event")],
)
def test_reports_failure_when_the_sequence_does_not_complete(
    registry: Registry,
    adapter: UplinkAdapter,
    comms: FakeComms,
    compiled_a: Compile,
    outcome: str,
    expected: str,
) -> None:
    assert evaluate(registry, compiled_a.bytecode_path, "sc1").passed
    comms.run_outcome = outcome
    result = adapter.uplink(compiled_a.bytecode_path, "sc1")
    assert not result.ok and expected in result.detail


def test_run_false_only_stores_the_file(
    registry: Registry, adapter: UplinkAdapter, comms: FakeComms, compiled_a: Compile
) -> None:
    assert evaluate(registry, compiled_a.bytecode_path, "sc1").passed
    result = adapter.uplink(compiled_a.bytecode_path, "sc1", run=False)
    assert result.ok and comms.sent_commands == []
