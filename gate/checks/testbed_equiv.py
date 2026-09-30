"""testbed_equiv: has exactly this bytecode already run successfully on a testbed that was
running the same dictionary the target vehicle is running now?

"Same bytecode" means the same SHA-256. "Same dictionary" means the same dictionary hash.
A testbed run on any other build says nothing about the target vehicle and does not count.
"""

from __future__ import annotations

from gate.checks import CheckContext, blocked, passed, skipped
from gate.results import CheckResult, Reason

NAME = "testbed_equiv"
SUCCESS = "SUCCESS"


def testbed_equiv(ctx: CheckContext) -> CheckResult:
    vehicle = ctx.registry.get_vehicle(ctx.vehicle_id)
    if vehicle is not None and vehicle.kind == "testbed":
        return skipped(
            NAME,
            Reason.TARGET_IS_TESTBED,
            f"'{ctx.vehicle_id}' is a testbed; a first run there is how testbed evidence is made",
        )

    running = ctx.running_dictionary_hash
    if running is None:
        return blocked(
            NAME,
            Reason.RUNNING_DICTIONARY_UNRESOLVED,
            "a testbed run only counts for the dictionary the vehicle is running, "
            "and that is not known",
        )

    testbeds = {v.id for v in ctx.registry.list_vehicles() if v.kind == "testbed"}
    runs = [
        run
        for run in ctx.registry.testbed_runs_for(ctx.bytecode_sha256, running)
        if run.vehicle_id in testbeds
    ]
    if not runs:
        return blocked(
            NAME,
            Reason.NO_TESTBED_RUN,
            f"this bytecode has not been run on a testbed running dictionary {running[:12]}",
            bytecode_sha256=ctx.bytecode_sha256,
            running_dictionary_hash=running,
        )

    latest = runs[-1]
    details = {
        "testbed_run_id": latest.id,
        "testbed": latest.vehicle_id,
        "finished_at": latest.finished_at.isoformat() if latest.finished_at else None,
        "running_dictionary_hash": running,
    }
    if latest.result != SUCCESS:
        return blocked(
            NAME,
            Reason.TESTBED_RUN_FAILED,
            f"the latest run of this bytecode on testbed '{latest.vehicle_id}' failed",
            **details,
        )
    return passed(
        NAME,
        f"ran successfully on testbed '{latest.vehicle_id}' with the same dictionary "
        f"({running[:12]})",
        **details,
    )
