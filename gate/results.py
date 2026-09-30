"""Result types shared by every check, the engine, the CLI, the API and the UI.

A gate decision is PASS or BLOCK. It is made of check results. Each check result carries a
`Reason`, which is a stable code that callers can branch on, plus a human message that is
only for display. Nothing downstream should ever parse the message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Result(StrEnum):
    """The outcome of a gate decision."""

    PASS = "PASS"
    BLOCK = "BLOCK"


class CheckStatus(StrEnum):
    """The outcome of one check. SKIPPED means the check had nothing to evaluate."""

    PASS = "PASS"
    BLOCK = "BLOCK"
    SKIPPED = "SKIPPED"


class Reason(StrEnum):
    """Why a check passed, blocked or was skipped. These values are part of the public API."""

    OK = "OK"

    # build_match: the compile record
    NO_COMPILE_RECORD = "NO_COMPILE_RECORD"
    BYTECODE_MODIFIED = "BYTECODE_MODIFIED"

    # build_match: which dictionary the vehicle is running
    UNKNOWN_VEHICLE = "UNKNOWN_VEHICLE"
    NO_BUILD_IDENTITY = "NO_BUILD_IDENTITY"
    STALE_BUILD_IDENTITY = "STALE_BUILD_IDENTITY"
    DIRTY_BUILD = "DIRTY_BUILD"
    UNKNOWN_BUILD = "UNKNOWN_BUILD"
    AMBIGUOUS_BUILD = "AMBIGUOUS_BUILD"
    BUILD_IDENTITY_CONFLICT = "BUILD_IDENTITY_CONFLICT"

    # build_match: the comparison itself
    DICTIONARY_MISMATCH = "DICTIONARY_MISMATCH"

    # testbed_equiv
    NO_TESTBED_RUN = "NO_TESTBED_RUN"
    TESTBED_RUN_FAILED = "TESTBED_RUN_FAILED"
    TARGET_IS_TESTBED = "TARGET_IS_TESTBED"

    # flight_rules and simulation
    BYTECODE_UNREADABLE = "BYTECODE_UNREADABLE"
    FLIGHT_RULE_VIOLATION = "FLIGHT_RULE_VIOLATION"
    NO_FLIGHT_RULES = "NO_FLIGHT_RULES"
    SIMULATION_VIOLATION = "SIMULATION_VIOLATION"
    NOT_SIMULATED = "NOT_SIMULATED"

    # any check that needs the running dictionary when build_match could not resolve it
    RUNNING_DICTIONARY_UNRESOLVED = "RUNNING_DICTIONARY_UNRESOLVED"

    # uplink adapter
    NO_PASS_DECISION = "NO_PASS_DECISION"
    STALE_DECISION = "STALE_DECISION"
    UPLINK_FAILED = "UPLINK_FAILED"


@dataclass(frozen=True)
class CheckResult:
    """What one check concluded."""

    check: str
    status: CheckStatus
    reason: Reason
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.status is CheckStatus.BLOCK

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "status": self.status.value,
            "reason": self.reason.value,
            "message": self.message,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckResult:
        return cls(
            check=data["check"],
            status=CheckStatus(data["status"]),
            reason=Reason(data["reason"]),
            message=data["message"],
            details=data.get("details", {}),
        )


@dataclass(frozen=True)
class GateDecision:
    """PASS or BLOCK for one bytecode file on one vehicle, with every check's result."""

    result: Result
    vehicle_id: str
    bytecode_sha256: str
    checks: tuple[CheckResult, ...]
    decided_at: datetime
    running_dictionary_hash: str | None = None
    compile_id: int | None = None
    id: int | None = None

    @property
    def passed(self) -> bool:
        return self.result is Result.PASS

    @property
    def blocking(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.blocked)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "result": self.result.value,
            "vehicle_id": self.vehicle_id,
            "bytecode_sha256": self.bytecode_sha256,
            "running_dictionary_hash": self.running_dictionary_hash,
            "compile_id": self.compile_id,
            "decided_at": self.decided_at.isoformat(),
            "checks": [c.to_dict() for c in self.checks],
        }
