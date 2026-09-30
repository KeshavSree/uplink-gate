"""Gate settings. Every value can be set by an environment variable."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

DEFAULT_CHECKS: tuple[str, ...] = ("build_match",)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class GateConfig:
    """Settings for the registry location and for how strict the checks are."""

    #: Directory holding the default SQLite registry and the stored dictionary files.
    home: Path = field(default_factory=lambda: Path(".gate"))
    #: SQLAlchemy URL of the registry. Empty means SQLite inside `home`.
    database_url: str = ""
    #: A build identity reading older than this many seconds is stale and blocks.
    max_state_age_s: float = 60.0
    #: Whether a project version ending in -dirty or -broken may pass.
    allow_dirty: bool = False
    #: Names of the checks the engine runs, in order.
    checks: tuple[str, ...] = DEFAULT_CHECKS
    #: An uplink needs a PASS decision younger than this many seconds.
    max_decision_age_s: float = 120.0

    @classmethod
    def from_env(cls) -> GateConfig:
        checks = os.environ.get("GATE_CHECKS")
        return cls(
            home=Path(os.environ.get("GATE_HOME", ".gate")),
            database_url=os.environ.get("GATE_DATABASE_URL", ""),
            max_state_age_s=float(os.environ.get("GATE_MAX_STATE_AGE_S", "60")),
            allow_dirty=_env_bool("GATE_ALLOW_DIRTY", False),
            checks=tuple(c.strip() for c in checks.split(",") if c.strip())
            if checks
            else DEFAULT_CHECKS,
            max_decision_age_s=float(os.environ.get("GATE_MAX_DECISION_AGE_S", "120")),
        )

    def with_(self, **changes: object) -> GateConfig:
        return replace(self, **changes)  # type: ignore[arg-type]

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.home / 'registry.db').resolve()}"

    @property
    def dictionary_store(self) -> Path:
        return self.home / "dictionaries"

    @property
    def bytecode_store(self) -> Path:
        return self.home / "bytecode"
