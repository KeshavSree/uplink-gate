"""End-to-end tests against the real demo vehicles.

Start the stack first:  docker compose up --build -d
Then run:               uv run pytest -m integration

Each vehicle is a container running a real build of F Prime's Ref deployment and a real
fprime-gds. These tests go through the gate service's HTTP API only.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration

API = os.environ.get("GATE_API_URL", "http://localhost:8000")
REPO = Path(__file__).resolve().parents[2]
BUILDS = {
    name: json.loads((REPO / "tests/fixtures/dictionaries" / name / "build.json").read_text())
    for name in ("A", "B")
}


def wait_for(condition: Callable[[], Any], timeout_s: float, what: str) -> Any:
    deadline = time.monotonic() + timeout_s
    last: Any = None
    while time.monotonic() < deadline:
        last = condition()
        if last:
            return last
        time.sleep(1.0)
    raise AssertionError(f"timed out after {timeout_s:.0f} s waiting for {what}")


def reflash(vehicle: str, build: str) -> None:
    subprocess.run([str(REPO / "scripts/reflash.sh"), vehicle, build], check=True, timeout=60)


@pytest.fixture(scope="module")
def api() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=API, timeout=120) as client:
        yield client


def put_on_build(api: httpx.Client, vehicle: str, build: str) -> None:
    reflash(vehicle, build)
    wait_for(
        lambda: running(api, vehicle) == BUILDS[build]["dictionary_sha256"],
        60,
        f"the gate to see {vehicle} on build {build}",
    )


def running(api: httpx.Client, vehicle: str) -> str | None:
    summary = api.get(f"/api/vehicles/{vehicle}").json()
    dictionary = summary.get("running_dictionary")
    return dictionary["hash"] if dictionary and summary["resolution"]["ok"] else None


@pytest.fixture(scope="module", autouse=True)
def fleet_ready(api: httpx.Client) -> None:
    """testbed and sc1 on build A, sc2 on build B, all reporting their identity."""

    def ready() -> bool:
        try:
            vehicles = {v["id"]: v for v in api.get("/api/vehicles").json()}
        except httpx.HTTPError:
            return False
        return len(vehicles) == 3 and all(v["resolution"]["ok"] for v in vehicles.values())

    wait_for(ready, 120, "all three vehicles to report a known build")
    for vehicle, build in (("testbed", "A"), ("sc1", "A"), ("sc2", "B")):
        if running(api, vehicle) != BUILDS[build]["dictionary_sha256"]:
            put_on_build(api, vehicle, build)
    assert api.get("/api/config").json()["checks"] == [
        "build_match",
        "testbed_equiv",
        "flight_rules",
    ]


SIGNAL_SETUP = """\
log("run {token}")
Ref.SG1.Settings(5, 2.5, 0.0, Ref.SignalType.SINE)
Ref.SG1.Toggle()
"""


def compile_for(
    api: httpx.Client, vehicle: str, source: str | None = None, name: str = "it"
) -> int:
    """Compile for a vehicle. Without `source`, a sequence no earlier test run has compiled,
    so there is no testbed evidence for it yet."""
    source = source or SIGNAL_SETUP.format(token=uuid.uuid4().hex)
    job = api.post(
        "/api/compile",
        params={"wait": "true"},
        json={"vehicle_id": vehicle, "sequence_name": name, "source": source},
    ).json()
    assert job["status"] == "done", job
    return int(job["compile"]["id"])


def check(api: httpx.Client, compile_id: int, vehicle: str) -> dict[str, Any]:
    decision = api.post("/api/check", json={"compile_id": compile_id, "vehicle_id": vehicle}).json()
    decision["by_check"] = {c["check"]: c for c in decision["checks"]}
    return decision


def uplink(api: httpx.Client, compile_id: int, vehicle: str) -> httpx.Response:
    return api.post("/api/uplink", json={"compile_id": compile_id, "vehicle_id": vehicle})


def qualify_on_testbed(api: httpx.Client, compile_id: int) -> None:
    """Run the bytecode on the testbed and wait for the gate to record the run."""
    sha = next(c for c in api.get("/api/compiles").json() if c["id"] == compile_id)[
        "bytecode_sha256"
    ]
    assert check(api, compile_id, "testbed")["result"] == "PASS"
    response = uplink(api, compile_id, "testbed")
    assert response.status_code == 200 and response.json()["ok"], response.text
    wait_for(
        lambda: any(
            r["bytecode_sha256"] == sha and r["result"] == "SUCCESS"
            for r in api.get("/api/testbed-runs").json()
        ),
        30,
        "the testbed run to be recorded",
    )


def test_three_live_vehicles_identified_by_dictionary_content(api: httpx.Client) -> None:
    vehicles = {v["id"]: v for v in api.get("/api/vehicles").json()}
    expected = {"testbed": "A", "sc1": "A", "sc2": "B"}
    for vehicle, build in expected.items():
        summary = vehicles[vehicle]
        assert summary["link"]["connected"], summary["link"]
        assert summary["state"]["source"] == "telemetry"
        assert summary["state"]["observed_project_version"] == BUILDS[build]["project_version"]
        assert summary["state"]["observed_dictionary_id"] == BUILDS[build]["dictionary_id"]
        assert summary["resolution"]["identified_by"] == "content"
        # The dictionaries committed as test fixtures are the ones these builds produced.
        assert summary["running_dictionary"]["hash"] == BUILDS[build]["dictionary_sha256"]
        assert summary["reading_age_s"] < summary["max_state_age_s"]


def test_compile_check_uplink_and_sequence_runs_on_a_matching_vehicle(api: httpx.Client) -> None:
    compile_id = compile_for(api, "sc1")

    # Right dictionary and no rule broken, but never run on a testbed: still blocked.
    first = check(api, compile_id, "sc1")
    assert first["result"] == "BLOCK"
    assert first["by_check"]["build_match"]["status"] == "PASS"
    assert first["by_check"]["flight_rules"]["status"] == "PASS"
    assert first["by_check"]["testbed_equiv"]["reason"] == "NO_TESTBED_RUN"
    assert uplink(api, compile_id, "sc1").status_code == 409

    qualify_on_testbed(api, compile_id)
    decision = check(api, compile_id, "sc1")
    assert decision["result"] == "PASS", decision
    assert [c["status"] for c in decision["checks"]] == ["PASS", "PASS", "PASS"]

    response = uplink(api, compile_id, "sc1")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"], result
    names = [event["name"] for event in result["events"]]
    assert names == ["FileHandling.fileUplink.FileReceived", "Ref.fpySeq.SequenceDone"]

    # The sequence really ran: the flight software logged the command it contains.
    events = api.get("/api/vehicles/sc1").json()["events"]
    assert any(
        e["name"] == "Ref.SG1.SettingsChanged" and "Frequency(Hz) 5" in e["message"] for e in events
    ), [e["name"] for e in events]


def test_blocked_on_a_mismatched_vehicle_and_nothing_is_sent(api: httpx.Client) -> None:
    compile_id = compile_for(api, "sc1")
    qualify_on_testbed(api, compile_id)
    decision = check(api, compile_id, "sc2")
    assert decision["result"] == "BLOCK"
    assert decision["by_check"]["build_match"]["reason"] == "DICTIONARY_MISMATCH"
    # The testbed run was on build A, so it is no evidence for sc2, which runs build B.
    assert decision["by_check"]["testbed_equiv"]["reason"] == "NO_TESTBED_RUN"
    # Read with sc2's dictionary, the command's argument bytes do not fit.
    assert decision["by_check"]["flight_rules"]["reason"] == "BYTECODE_UNREADABLE"

    before = api.get("/api/vehicles/sc2").json()["events"]
    response = uplink(api, compile_id, "sc2")
    assert response.status_code == 409
    assert response.json()["refused"] is True
    time.sleep(3)
    after = api.get("/api/vehicles/sc2").json()["events"]
    new = [e for e in after if e not in before]
    assert not any("fileUplink" in e["name"] or "fpySeq" in e["name"] for e in new), new


def test_flight_rule_violation_blocks_even_the_testbed(api: httpx.Client) -> None:
    job = api.post(
        "/api/compile",
        params={"wait": "true"},
        json={"vehicle_id": "testbed", "sequence_name": "signal_too_fast"},
    ).json()
    compile_id = job["compile"]["id"]
    decision = check(api, compile_id, "testbed")
    assert decision["result"] == "BLOCK"
    rules = decision["by_check"]["flight_rules"]
    assert rules["reason"] == "FLIGHT_RULE_VIOLATION"
    assert {v["rule_id"] for v in rules["details"]["violations"]} == {
        "sg-frequency-range",
        "sg-retune-spacing",
    }
    assert uplink(api, compile_id, "testbed").status_code == 409


def test_compiles_for_all_vehicles_run_concurrently(api: httpx.Client) -> None:
    jobs = api.post(
        "/api/compile/all", params={"wait": "true"}, json={"sequence_name": "signal_setup"}
    ).json()
    by_vehicle = {job["vehicle_id"]: job for job in jobs}
    assert set(by_vehicle) == {"testbed", "sc1", "sc2"}
    assert all(job["status"] == "done" for job in jobs), jobs
    assert by_vehicle["sc1"]["dictionary_hash"] == BUILDS["A"]["dictionary_sha256"]
    assert by_vehicle["sc2"]["dictionary_hash"] == BUILDS["B"]["dictionary_sha256"]
    assert len({job["worker_pid"] for job in jobs}) > 1, "jobs should run in different processes"


def test_live_swap_flips_pass_to_block_without_a_restart(api: httpx.Client) -> None:
    compile_id = compile_for(api, "sc1")
    qualify_on_testbed(api, compile_id)
    assert check(api, compile_id, "sc1")["result"] == "PASS"

    put_on_build(api, "sc1", "B")
    try:
        decision = check(api, compile_id, "sc1")
        assert decision["result"] == "BLOCK"
        assert decision["by_check"]["build_match"]["reason"] == "DICTIONARY_MISMATCH"
        assert uplink(api, compile_id, "sc1").status_code == 409

        # Recompiled for the new build, the dictionary matches, but the testbed still runs
        # the old build, so there is no way to get testbed evidence for it yet.
        new_compile = compile_for(api, "sc1")
        decision = check(api, new_compile, "sc1")
        assert decision["by_check"]["build_match"]["status"] == "PASS"
        assert decision["by_check"]["testbed_equiv"]["reason"] == "NO_TESTBED_RUN"
        assert check(api, new_compile, "testbed")["by_check"]["build_match"]["reason"] == (
            "DICTIONARY_MISMATCH"
        )

        # Bring the testbed to the same build, run there, and the gate opens.
        put_on_build(api, "testbed", "B")
        qualify_on_testbed(api, new_compile)
        assert check(api, new_compile, "sc1")["result"] == "PASS"
        response = uplink(api, new_compile, "sc1")
        assert response.status_code == 200 and response.json()["ok"], response.text
    finally:
        put_on_build(api, "testbed", "A")
        put_on_build(api, "sc1", "A")
    assert check(api, compile_id, "sc1")["result"] == "PASS"
