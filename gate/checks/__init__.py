"""The gate's checks. Each check looks at one aspect of a bytecode file and a target vehicle."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gate.config import GateConfig
from gate.results import CheckResult, CheckStatus, Reason
from gate.simulation import Sample

if TYPE_CHECKING:
    from gate.registry import Compile, Registry
    from gate.resolve import Resolution


@dataclass
class CheckContext:
    """Everything a check may read. Earlier checks leave their findings here for later ones."""

    registry: Registry
    config: GateConfig
    vehicle_id: str
    bytecode_path: Path
    bytecode_sha256: str
    now: datetime
    #: Set by build_match: what the vehicle is running, or why that is unknown.
    resolution: Resolution | None = None
    #: Set by build_match: the compile record that matches the running dictionary, if any.
    matching_compile: Compile | None = None
    #: Latest telemetry of the vehicle by channel name, when a live link provides it.
    telemetry: Mapping[str, Sample] | None = None
    scratch: dict[str, Any] = field(default_factory=dict)

    @property
    def running_dictionary_hash(self) -> str | None:
        return self.resolution.dictionary_hash if self.resolution else None


Check = Callable[[CheckContext], CheckResult]


def passed(check: str, message: str, **details: Any) -> CheckResult:
    return CheckResult(check, CheckStatus.PASS, Reason.OK, message, details)


def blocked(check: str, reason: Reason, message: str, **details: Any) -> CheckResult:
    return CheckResult(check, CheckStatus.BLOCK, reason, message, details)


def skipped(check: str, reason: Reason, message: str, **details: Any) -> CheckResult:
    return CheckResult(check, CheckStatus.SKIPPED, reason, message, details)
