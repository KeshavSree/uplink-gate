"""The HTTP layer, with a fake vehicle link. No network, no Docker."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from gate.config import GateConfig
from tests.conftest import FIXTURES, SEQUENCES, VERSION_A, VERSION_B
from tests.fakes import FakeComms

HASH_A = "4bbb2268b356ad9e8ca4c46a2ca666665235f8cc599d6ce11ccf740482bdef37"
HASH_B = "d6c09e7f3db838a98a4548a05c443bfc1aebfb86dc58b6cc23ae20d93243e2f8"


@pytest.fixture
def seeds(tmp_path: Path) -> Path:
    """Only builds A and B, as a build system would publish them."""
    seeds = tmp_path / "published"
    for build in ("A", "B"):
        target = seeds / build
        target.mkdir(parents=True)
        source = FIXTURES / "dictionaries" / build / "RefTopologyDictionary.json"
        (target / source.name).write_bytes(source.read_bytes())
    return seeds


@pytest.fixture
def client(config: GateConfig, seeds: Path) -> Iterator[TestClient]:
    settings = Settings(
        vehicles={"testbed": "testbed:50050", "sc1": "sc1:50050", "sc2": "sc2:50050"},
        seed_dictionaries=seeds,
        sequences_dir=SEQUENCES,
        web_dir=None,
        compile_workers=3,
        start_ingest=False,
    )
    app = create_app(config, settings)
    service = app.state.service
    comms = FakeComms(service.registry)
    service.comms = comms
    with TestClient(app) as client:
        for vehicle, version, dictionary_hash in (
            ("testbed", VERSION_A, HASH_A),
            ("sc1", VERSION_A, HASH_A),
            ("sc2", VERSION_B, HASH_B),
        ):
            comms.identity[vehicle] = (version, f"dict-sha256:{dictionary_hash[:16]}")
            comms.refresh_identity(vehicle, 1)
        client.comms = comms  # type: ignore[attr-defined]
        yield client


def compile_for(client: TestClient, vehicle: str) -> dict:
    response = client.post(
        "/api/compile?wait=true", json={"vehicle_id": vehicle, "sequence_name": "signal_setup"}
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "done", job
    return job


def test_startup_seeds_dictionaries_and_vehicles(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"ok": True, "vehicles": 3}
    assert {d["hash"] for d in client.get("/api/dictionaries").json()} == {HASH_A, HASH_B}
    vehicles = {v["id"]: v for v in client.get("/api/vehicles").json()}
    assert vehicles["testbed"]["kind"] == "testbed"
    assert vehicles["sc1"]["running_dictionary"]["hash"] == HASH_A
    assert vehicles["sc2"]["running_dictionary"]["hash"] == HASH_B
    assert vehicles["sc2"]["resolution"]["identified_by"] == "content"
    assert "signal_setup" in {s["name"] for s in client.get("/api/sequences").json()}


def test_compile_check_uplink_on_a_matching_vehicle(client: TestClient) -> None:
    job = compile_for(client, "sc1")
    assert job["dictionary_hash"] == HASH_A and job["worker_pid"]
    compile_id = job["compile"]["id"]
    assert client.get(f"/api/compile/jobs/{job['id']}").json()["status"] == "done"

    decision = client.post("/api/check", json={"compile_id": compile_id, "vehicle_id": "sc1"})
    assert decision.json()["result"] == "PASS"

    uplink = client.post("/api/uplink", json={"compile_id": compile_id, "vehicle_id": "sc1"})
    assert uplink.status_code == 200
    body = uplink.json()
    assert body["ok"] and not body["refused"]
    assert client.comms.sent_commands[-1][1] == "Ref.fpySeq.RUN"  # type: ignore[attr-defined]

    detail = client.get("/api/vehicles/sc1").json()
    assert detail["compiles"][0]["id"] == compile_id
    assert detail["uplinks"][0]["ok"] is True
    assert detail["last_decision"]["result"] == "PASS"


def test_check_blocks_and_uplink_is_refused_on_a_mismatched_vehicle(client: TestClient) -> None:
    compile_id = compile_for(client, "sc1")["compile"]["id"]

    decision = client.post(
        "/api/check", json={"compile_id": compile_id, "vehicle_id": "sc2"}
    ).json()
    assert decision["result"] == "BLOCK"
    assert decision["checks"][0]["reason"] == "DICTIONARY_MISMATCH"

    uplink = client.post("/api/uplink", json={"compile_id": compile_id, "vehicle_id": "sc2"})
    assert uplink.status_code == 409
    assert uplink.json()["refused"] is True
    assert uplink.json()["reason"] == "NO_PASS_DECISION"
    assert client.comms.sent_files == []  # type: ignore[attr-defined]


def test_uplink_without_a_check_is_refused(client: TestClient) -> None:
    compile_id = compile_for(client, "sc1")["compile"]["id"]
    uplink = client.post("/api/uplink", json={"compile_id": compile_id, "vehicle_id": "sc1"})
    assert uplink.status_code == 409
    assert uplink.json()["reason"] == "NO_PASS_DECISION"


def test_live_swap_flips_pass_to_block(client: TestClient) -> None:
    compile_id = compile_for(client, "sc1")["compile"]["id"]
    body = {"compile_id": compile_id, "vehicle_id": "sc1"}
    assert client.post("/api/check", json=body).json()["result"] == "PASS"

    comms = client.comms  # type: ignore[attr-defined]
    comms.identity["sc1"] = (VERSION_B, f"dict-sha256:{HASH_B[:16]}")
    comms.refresh_identity("sc1", 1)

    assert client.post("/api/check", json=body).json()["result"] == "BLOCK"
    assert client.get("/api/vehicles/sc1").json()["running_dictionary"]["hash"] == HASH_B


def test_compile_all_runs_one_job_per_vehicle_concurrently(client: TestClient) -> None:
    response = client.post("/api/compile/all?wait=true", json={"sequence_name": "signal_setup"})
    assert response.status_code == 202
    jobs = {job["vehicle_id"]: job for job in response.json()}
    assert set(jobs) == {"testbed", "sc1", "sc2"}
    assert all(job["status"] == "done" for job in jobs.values())
    assert jobs["sc1"]["dictionary_hash"] == HASH_A
    assert jobs["sc2"]["dictionary_hash"] == HASH_B
    # Same sequence, different dictionaries, different bytes.
    assert jobs["sc1"]["compile"]["bytecode_sha256"] != jobs["sc2"]["compile"]["bytecode_sha256"]
    assert (
        jobs["sc1"]["compile"]["bytecode_sha256"] == jobs["testbed"]["compile"]["bytecode_sha256"]
    )

    kinds = [e["type"] for e in client.get("/api/events/recent").json()]
    assert kinds.count("compile.queued") == 3
    assert kinds.count("compile.finished") == 3
    # All three were queued before any finished: the jobs overlapped.
    assert max(i for i, k in enumerate(kinds) if k == "compile.queued") < min(
        i for i, k in enumerate(kinds) if k == "compile.finished"
    )


def test_compile_all_reports_a_vehicle_with_unknown_build(client: TestClient) -> None:
    comms = client.comms  # type: ignore[attr-defined]
    comms.identity["sc2"] = ("gate-demo-9.9.9", None)
    comms.refresh_identity("sc2", 1)
    jobs = {
        job["vehicle_id"]: job
        for job in client.post(
            "/api/compile/all?wait=true", json={"sequence_name": "signal_setup"}
        ).json()
    }
    assert jobs["sc2"]["status"] == "failed" and "gate-demo-9.9.9" in jobs["sc2"]["error"]
    assert jobs["sc1"]["status"] == "done"


def test_compile_from_source_text_and_compiler_errors(client: TestClient) -> None:
    ok = client.post(
        "/api/compile?wait=true",
        json={
            "dictionary": HASH_A[:10],
            "sequence_name": "noop",
            "source": "CdhCore.cmdDisp.CMD_NO_OP()\n",
        },
    ).json()
    assert ok["status"] == "done" and ok["compile"]["dictionary_hash"] == HASH_A

    bad = client.post(
        "/api/compile?wait=true",
        json={"vehicle_id": "sc1", "sequence_name": "bad", "source": "Ref.nothing.NOPE()\n"},
    ).json()
    assert bad["status"] == "failed" and bad["log"]


def test_compile_request_errors(client: TestClient) -> None:
    assert client.post("/api/compile", json={"vehicle_id": "sc1"}).status_code == 422
    assert client.post("/api/compile", json={"sequence_name": "signal_setup"}).status_code == 422
    missing = client.post("/api/compile", json={"vehicle_id": "sc1", "sequence_name": "nope"})
    assert missing.status_code == 404
    unknown = client.post("/api/compile", json={"vehicle_id": "x", "sequence_name": "signal_setup"})
    assert unknown.status_code == 404
    assert (
        client.post("/api/check", json={"compile_id": 99, "vehicle_id": "sc1"}).status_code == 404
    )
    assert client.get("/api/vehicles/nope").status_code == 404
    assert client.get("/api/compile/jobs/99").status_code == 404


def test_websocket_streams_gate_events(client: TestClient) -> None:
    compile_id = compile_for(client, "sc1")["compile"]["id"]
    with client.websocket_connect("/api/events") as socket:
        client.post("/api/check", json={"compile_id": compile_id, "vehicle_id": "sc2"})
        event = socket.receive_json()
    assert event["type"] == "gate.decision"
    assert event["decision"]["result"] == "BLOCK"
    assert event["decision"]["vehicle_id"] == "sc2"


def test_routine_version_poll_events_are_recognised() -> None:
    from api.app import _is_routine

    assert _is_routine("CdhCore.version.ProjectVersion")
    assert _is_routine("CdhCore.cmdDisp.OpCodeCompleted")
    assert not _is_routine("CdhCore.cmdDisp.OpCodeError")
    assert not _is_routine("Ref.fpySeq.SequenceDone")


def test_a_restarted_service_does_not_overwrite_earlier_bytecode(
    config: GateConfig, seeds: Path
) -> None:
    settings = Settings(
        seed_dictionaries=seeds,
        sequences_dir=SEQUENCES,
        web_dir=None,
        compile_workers=1,
        start_ingest=False,
    )
    paths = []
    for _ in range(2):  # two service lifetimes, each starting its job numbers at 1
        with TestClient(create_app(config, settings)) as client:
            job = client.post(
                "/api/compile?wait=true",
                json={"dictionary": HASH_A[:10], "sequence_name": "signal_setup"},
            ).json()
            assert job["id"] == 1
            paths.append(job["compile"]["bytecode_path"])
    assert paths[0] != paths[1]
    assert Path(paths[0]).is_file() and Path(paths[1]).is_file()
