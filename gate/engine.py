"""The gate engine: run the checks against one bytecode file and one vehicle.

Every configured check runs, even after one has blocked, so the decision shows each check's
result separately. The decision is BLOCK if any check blocked.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from gate.checks import Check, CheckContext, blocked
from gate.config import GateConfig
from gate.dictionary import sha256_file
from gate.registry import Registry, utcnow
from gate.results import CheckResult, GateDecision, Reason, Result


def available_checks() -> dict[str, Check]:
    """All checks by name. Imported lazily so the core stays importable without extras."""
    from gate.checks.build_match import build_match

    return {"build_match": build_match}


def evaluate(
    registry: Registry,
    bytecode_path: Path | str,
    vehicle_id: str,
    *,
    config: GateConfig | None = None,
    checks: Sequence[str] | None = None,
    now: datetime | None = None,
    record: bool = True,
) -> GateDecision:
    """Decide PASS or BLOCK for uplinking `bytecode_path` to `vehicle_id`.

    `record=False` returns the decision without storing it in the registry.
    """
    config = config or registry.config
    now = now or utcnow()
    bytecode_path = Path(bytecode_path)
    names = tuple(checks) if checks is not None else config.checks
    known = available_checks()
    unknown = [name for name in names if name not in known]
    if unknown:
        raise ValueError(f"unknown check(s): {', '.join(unknown)} (known: {', '.join(known)})")

    ctx = CheckContext(
        registry=registry,
        config=config,
        vehicle_id=vehicle_id,
        bytecode_path=bytecode_path,
        bytecode_sha256=sha256_file(bytecode_path),
        now=now,
    )

    results: list[CheckResult] = []
    for name in names:
        try:
            results.append(known[name](ctx))
        except Exception as error:  # a broken check must block, never silently pass
            results.append(
                blocked(name, Reason.BYTECODE_UNREADABLE, f"check failed to run: {error!r}")
            )

    decision = GateDecision(
        result=Result.BLOCK if any(r.blocked for r in results) else Result.PASS,
        vehicle_id=vehicle_id,
        bytecode_sha256=ctx.bytecode_sha256,
        checks=tuple(results),
        decided_at=now,
        running_dictionary_hash=ctx.running_dictionary_hash,
        compile_id=ctx.matching_compile.id if ctx.matching_compile else None,
    )
    if ctx.resolution is not None and ctx.resolution.state is not None:
        registry.set_resolved_dictionary(vehicle_id, ctx.resolution.dictionary_hash)
    return registry.add_decision(decision) if record else decision
