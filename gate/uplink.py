"""The uplink adapter: the only code path that sends bytecode to a vehicle.

It refuses unless there is a PASS gate decision for exactly these bytes and this vehicle
that is recent enough. Then it asks the vehicle for its build identity again and re-runs the
checks immediately before sending, so a vehicle that changed build after the decision was
made is still caught.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from gate.config import GateConfig
from gate.dictionary import sha256_file
from gate.engine import evaluate
from gate.registry import Registry, utcnow
from gate.results import GateDecision, Reason
from gate.telemetry import EventReading, LinkDown, VehicleComms

FILE_RECEIVED_EVENT = "FileHandling.fileUplink.FileReceived"
FAILURE_SEVERITIES = {"WARNING_HI", "WARNING_LO", "FATAL"}


class UplinkRefused(Exception):
    """The adapter would not send. Nothing was transmitted to the vehicle."""

    def __init__(self, reason: Reason, message: str, decision: GateDecision | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.decision = decision


@dataclass(frozen=True)
class UplinkResult:
    """What happened after the adapter agreed to send."""

    ok: bool
    vehicle_id: str
    bytecode_sha256: str
    decision_id: int
    remote_path: str
    detail: str
    running_dictionary_hash: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    events: tuple[EventReading, ...] = field(default_factory=tuple)
    uplink_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "vehicle_id": self.vehicle_id,
            "bytecode_sha256": self.bytecode_sha256,
            "decision_id": self.decision_id,
            "remote_path": self.remote_path,
            "detail": self.detail,
            "uplink_id": self.uplink_id,
            "events": [e.to_dict() for e in self.events],
        }


class UplinkAdapter:
    def __init__(
        self,
        registry: Registry,
        comms: VehicleComms,
        config: GateConfig | None = None,
        identity_timeout_s: float = 8.0,
        file_timeout_s: float = 30.0,
        run_timeout_s: float = 60.0,
    ) -> None:
        self.registry = registry
        self.comms = comms
        self.config = config or registry.config
        self.identity_timeout_s = identity_timeout_s
        self.file_timeout_s = file_timeout_s
        self.run_timeout_s = run_timeout_s

    def uplink(
        self,
        bytecode_path: Path | str,
        vehicle_id: str,
        *,
        decision_id: int | None = None,
        run: bool = True,
    ) -> UplinkResult:
        """Send bytecode to a vehicle and, if `run`, start it on the Fpy sequencer.

        Raises `UplinkRefused` without sending anything when the gate does not allow it.
        """
        bytecode_path = Path(bytecode_path)
        bytecode_sha256 = sha256_file(bytecode_path)
        self._require_pass_decision(vehicle_id, bytecode_sha256, decision_id)

        try:
            fresh = self.comms.refresh_identity(vehicle_id, self.identity_timeout_s)
        except LinkDown as error:
            raise UplinkRefused(Reason.UPLINK_FAILED, str(error)) from error
        if not fresh:
            raise UplinkRefused(
                Reason.STALE_BUILD_IDENTITY,
                f"'{vehicle_id}' did not report its build identity within "
                f"{self.identity_timeout_s:.0f} s of being asked",
            )

        recheck = evaluate(self.registry, bytecode_path, vehicle_id, config=self.config)
        assert recheck.id is not None
        if not recheck.passed:
            first = recheck.blocking[0]
            self.registry.add_uplink(
                recheck.id, vehicle_id, ok=False, sent_at=utcnow(), detail="refused at re-check"
            )
            raise UplinkRefused(
                first.reason, f"re-check before sending blocked: {first.message}", recheck
            )

        remote_path = f"{self.config.uplink_dir.rstrip('/')}/{bytecode_sha256[:16]}.bin"
        started_at = utcnow()
        ok, detail, events = self._send(vehicle_id, bytecode_path, remote_path, run, started_at)
        row = self.registry.add_uplink(
            recheck.id, vehicle_id, ok=ok, sent_at=started_at, detail=detail
        )
        return UplinkResult(
            ok=ok,
            vehicle_id=vehicle_id,
            bytecode_sha256=bytecode_sha256,
            decision_id=recheck.id,
            remote_path=remote_path,
            detail=detail,
            running_dictionary_hash=recheck.running_dictionary_hash,
            started_at=started_at,
            finished_at=utcnow(),
            events=tuple(events),
            uplink_id=row.id,
        )

    def _require_pass_decision(
        self, vehicle_id: str, bytecode_sha256: str, decision_id: int | None
    ) -> GateDecision:
        if decision_id is not None:
            decision = self.registry.get_decision(decision_id)
            if (
                decision is None
                or decision.vehicle_id != vehicle_id
                or decision.bytecode_sha256 != bytecode_sha256
            ):
                raise UplinkRefused(
                    Reason.NO_PASS_DECISION,
                    f"decision {decision_id} is not a decision for this bytecode on '{vehicle_id}'",
                )
        else:
            decision = self.registry.latest_decision(vehicle_id, bytecode_sha256)
            if decision is None:
                raise UplinkRefused(
                    Reason.NO_PASS_DECISION,
                    f"this bytecode has never been checked for '{vehicle_id}'; run the gate first",
                )
        if not decision.passed:
            raise UplinkRefused(
                Reason.NO_PASS_DECISION,
                f"the gate decision for this bytecode on '{vehicle_id}' is BLOCK "
                f"({', '.join(c.reason.value for c in decision.blocking)})",
                decision,
            )
        age_s = (utcnow() - decision.decided_at).total_seconds()
        if age_s > self.config.max_decision_age_s:
            raise UplinkRefused(
                Reason.STALE_DECISION,
                f"the PASS decision is {age_s:.0f} s old "
                f"(limit {self.config.max_decision_age_s:.0f} s); run the gate again",
                decision,
            )
        return decision

    def _send(
        self, vehicle_id: str, bytecode_path: Path, remote_path: str, run: bool, since: datetime
    ) -> tuple[bool, str, list[EventReading]]:
        events: list[EventReading] = []
        try:
            self.comms.uplink_file(vehicle_id, bytecode_path, remote_path)
            received = self.comms.wait_for_event(
                vehicle_id,
                {FILE_RECEIVED_EVENT},
                since,
                self.file_timeout_s,
                lambda e: remote_path in e.message,
            )
            if received is None:
                return False, f"the vehicle did not confirm receiving {remote_path}", events
            events.append(received)
            if not run:
                return True, f"file stored on the vehicle as {remote_path}", events

            sequencer = self.config.sequencer
            run_at = utcnow()
            self.comms.send_command(vehicle_id, f"{sequencer}.RUN", [remote_path, "BLOCK"])
            done = f"{sequencer}.SequenceDone"
            outcome = self.comms.wait_for_event(
                vehicle_id,
                None,
                run_at,
                self.run_timeout_s,
                lambda e: (
                    e.name.startswith(sequencer + ".")
                    and (e.name == done or e.severity in FAILURE_SEVERITIES)
                ),
            )
        except LinkDown as error:
            return False, str(error), events

        if outcome is None:
            return (
                False,
                f"no completion event from {sequencer} in {self.run_timeout_s:.0f} s",
                events,
            )
        events.append(outcome)
        if outcome.name == done:
            return True, outcome.message, events
        return False, f"{outcome.name.rsplit('.', 1)[-1]}: {outcome.message}", events
