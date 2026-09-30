"""Turning version telemetry into a build identity, and keeping a vehicle link up."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest

from gate.config import GateConfig
from gate.registry import Dictionary, Registry, utcnow
from gate.telemetry import (
    ChannelReading,
    IdentityTracker,
    IngestManager,
    LinkDown,
    VehicleIngest,
)
from tests.conftest import NOW, VERSION_A, VERSION_B
from tests.fakes import FakeLink

PV = "CdhCore.version.ProjectVersion"
CV = "CdhCore.version.CustomVersion01"


def custom(dictionary_id: str) -> dict[str, str]:
    return {
        "version_enum": "PROJECT_VERSION_00",
        "version_value": dictionary_id,
        "version_status": "OK",
    }


@pytest.fixture
def tracker(registry: Registry, config: GateConfig) -> IdentityTracker:
    registry.upsert_vehicle("sc1")
    return IdentityTracker(registry, "sc1", config)


def feed(tracker: IdentityTracker, name: str, value: object, age_s: float = 0) -> bool:
    return tracker.on_channel(ChannelReading("sc1", name, value, NOW - timedelta(seconds=age_s)))


def test_project_version_alone_sets_the_identity(
    registry: Registry, tracker: IdentityTracker, dict_a: Dictionary
) -> None:
    assert feed(tracker, PV, VERSION_A)
    state = registry.get_vehicle_state("sc1")
    assert state is not None
    assert state.observed_project_version == VERSION_A
    assert state.observed_dictionary_id is None
    assert state.source == "telemetry"
    assert state.resolved_dictionary_hash == dict_a.hash


def test_dictionary_id_is_paired_with_the_project_version(
    registry: Registry, tracker: IdentityTracker, dict_a: Dictionary
) -> None:
    feed(tracker, PV, VERSION_A)
    assert feed(tracker, CV, custom(f"dict-sha256:{dict_a.hash[:16]}"))
    state = registry.get_vehicle_state("sc1")
    assert state is not None
    assert state.observed_dictionary_id == f"dict-sha256:{dict_a.hash[:16]}"
    assert state.resolved_dictionary_hash == dict_a.hash


def test_dictionary_id_before_project_version_waits_for_it(
    registry: Registry, tracker: IdentityTracker, dict_a: Dictionary
) -> None:
    assert not feed(tracker, CV, custom(f"dict-sha256:{dict_a.hash[:16]}"))
    assert registry.get_vehicle_state("sc1") is None
    assert feed(tracker, PV, VERSION_A)
    state = registry.get_vehicle_state("sc1")
    assert state is not None and state.observed_dictionary_id is not None


def test_old_dictionary_id_is_not_combined_with_a_new_project_version(
    registry: Registry, tracker: IdentityTracker, dict_a: Dictionary, dict_b: Dictionary
) -> None:
    """After a reflash the first new reading must not inherit the previous build's ID."""
    feed(tracker, PV, VERSION_A, age_s=30)
    feed(tracker, CV, custom(f"dict-sha256:{dict_a.hash[:16]}"), age_s=30)
    feed(tracker, PV, VERSION_B)
    state = registry.get_vehicle_state("sc1")
    assert state is not None
    assert state.observed_project_version == VERSION_B
    assert state.observed_dictionary_id is None
    assert state.resolved_dictionary_hash == dict_b.hash


def test_other_channels_are_ignored(registry: Registry, tracker: IdentityTracker) -> None:
    assert not feed(tracker, "Ref.SG1.Output", 1.5)
    assert registry.get_vehicle_state("sc1") is None


# --- VehicleIngest through IngestManager, with a fake link --------------------------------


def wait_until(condition: Callable[[], object], timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


@pytest.fixture
def manager(
    registry: Registry, config: GateConfig, monkeypatch: pytest.MonkeyPatch
) -> Iterator[IngestManager]:
    monkeypatch.setattr(VehicleIngest, "LINK_WAIT_S", 0.1)
    FakeLink.instances.clear()
    FakeLink.fail_connect = False
    published: list[tuple[str, dict]] = []
    manager = IngestManager(
        registry,
        config.with_(identity_poll_s=0.2),
        publish=lambda kind, payload: published.append((kind, payload)),
        link_factory=FakeLink,
    )
    manager.published = published  # type: ignore[attr-defined]
    yield manager
    manager.stop()


def test_ingest_asks_for_versions_and_records_the_answer(
    registry: Registry, manager: IngestManager, dict_a: Dictionary
) -> None:
    registry.upsert_vehicle("sc1", gds_endpoint="sc1:50050")
    manager.start()
    wait_until(lambda: FakeLink.instances and FakeLink.instances[0].alive)
    link = FakeLink.instances[0]
    assert link.dictionary_path == dict_a.path
    wait_until(lambda: link.commands)
    assert link.commands[0] == ("CdhCore.version.VERSION", ["ALL"])

    link.reports = (VERSION_A, f"dict-sha256:{dict_a.hash[:16]}")
    wait_until(lambda: registry.get_vehicle_state("sc1") is not None)
    state = registry.get_vehicle_state("sc1")
    assert state is not None and state.resolved_dictionary_hash == dict_a.hash
    status = manager.status("sc1")
    assert status is not None and status["connected"] and status["channels_received"] >= 1
    kinds = {kind for kind, _ in manager.published}  # type: ignore[attr-defined]
    assert {"vehicle.link", "vehicle.state"} <= kinds


def test_ingest_switches_decode_dictionary_after_a_reflash(
    registry: Registry, manager: IngestManager, dict_a: Dictionary, dict_b: Dictionary
) -> None:
    registry.upsert_vehicle("sc1", gds_endpoint="sc1:50050")
    manager.start()
    wait_until(lambda: FakeLink.instances and FakeLink.instances[0].alive)
    first = FakeLink.instances[0]
    first.reports = (VERSION_B, f"dict-sha256:{dict_b.hash[:16]}")
    wait_until(lambda: len(FakeLink.instances) >= 2 and FakeLink.instances[-1].alive)
    assert FakeLink.instances[-1].dictionary_path == dict_b.path
    assert not first.alive


def test_ingest_reconnects_when_the_link_drops(
    registry: Registry, manager: IngestManager, dict_a: Dictionary
) -> None:
    registry.upsert_vehicle("sc1", gds_endpoint="sc1:50050")
    manager.start()
    wait_until(lambda: FakeLink.instances and FakeLink.instances[0].alive)
    FakeLink.instances[0].alive = False
    wait_until(lambda: len(FakeLink.instances) >= 2 and FakeLink.instances[-1].alive)


def test_ingest_reports_a_refused_connection(
    registry: Registry, manager: IngestManager, dict_a: Dictionary
) -> None:
    FakeLink.fail_connect = True
    registry.upsert_vehicle("sc1", gds_endpoint="sc1:50050")
    manager.start()
    wait_until(lambda: (manager.status("sc1") or {}).get("error"))
    status = manager.status("sc1")
    assert status is not None and not status["connected"]
    with pytest.raises(LinkDown):
        manager.send_command("sc1", "CdhCore.cmdDisp.CMD_NO_OP", [])


def test_ingest_waits_when_the_registry_has_no_dictionary(
    registry: Registry, manager: IngestManager
) -> None:
    registry.upsert_vehicle("sc1", gds_endpoint="sc1:50050")
    manager.start()
    wait_until(lambda: (manager.status("sc1") or {}).get("error"))
    assert FakeLink.instances == []


def test_refresh_identity_waits_for_a_reading_newer_than_the_request(
    registry: Registry, manager: IngestManager, dict_a: Dictionary
) -> None:
    registry.upsert_vehicle("sc1", gds_endpoint="sc1:50050")
    manager.start()
    wait_until(lambda: FakeLink.instances and FakeLink.instances[0].alive)
    link = FakeLink.instances[0]
    assert manager.refresh_identity("sc1", timeout_s=0.3) is False

    link.reports = (VERSION_A, None)
    before = utcnow()
    assert manager.refresh_identity("sc1", timeout_s=3) is True
    state = registry.get_vehicle_state("sc1")
    assert state is not None and state.observed_at >= before


def test_wait_for_event_returns_matching_event_or_none(
    registry: Registry, manager: IngestManager, dict_a: Dictionary
) -> None:
    registry.upsert_vehicle("sc1", gds_endpoint="sc1:50050")
    manager.start()
    wait_until(lambda: FakeLink.instances and FakeLink.instances[0].alive)
    since = utcnow()
    FakeLink.instances[0].feed_event("Ref.fpySeq.SequenceDone", "ACTIVITY_HI", "Completed x.bin")
    found = manager.wait_for_event("sc1", {"Ref.fpySeq.SequenceDone"}, since, 1.0)
    assert found is not None and found.message == "Completed x.bin"
    assert manager.wait_for_event("sc1", {"Ref.fpySeq.Nope"}, since, 0.1) is None
    assert manager.wait_for_event("sc1", None, since, 0.1, lambda e: "y.bin" in e.message) is None
    assert [e.name for e in manager.recent_events("sc1")] == ["Ref.fpySeq.SequenceDone"]


def test_vehicle_without_an_endpoint_has_no_link(
    registry: Registry, manager: IngestManager
) -> None:
    registry.upsert_vehicle("sc1")
    manager.start()
    assert manager.status("sc1") is None
    with pytest.raises(LinkDown):
        manager.refresh_identity("sc1", 0.1)
