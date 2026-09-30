"""Work out which dictionary a vehicle is running, from its latest build identity reading.

A vehicle reports its project version (the `git describe` label F Prime stamps into a
build). A project version is a label, not a hash, so it can be missing from the registry,
can map to more than one dictionary, or can describe a build with uncommitted changes. Any
of those blocks. A build can also report a dictionary ID, which is a prefix of the
dictionary hash. When it does, the dictionary is identified by content and the label is
only cross-checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from gate.config import GateConfig
from gate.dictionary import hash_prefix_from_dictionary_id, is_dirty_version
from gate.registry import Registry, VehicleState
from gate.results import Reason


@dataclass(frozen=True)
class Resolution:
    """The running dictionary of a vehicle, or the reason it could not be determined."""

    vehicle_id: str
    reason: Reason
    message: str
    dictionary_hash: str | None = None
    state: VehicleState | None = None
    #: "content" if the dictionary ID identified it, "label" if only the project version did.
    identified_by: str | None = None

    @property
    def ok(self) -> bool:
        return self.dictionary_hash is not None

    def details(self) -> dict[str, Any]:
        details: dict[str, Any] = {"vehicle_id": self.vehicle_id}
        if self.state is not None:
            details["observed_project_version"] = self.state.observed_project_version
            details["observed_dictionary_id"] = self.state.observed_dictionary_id
            details["observed_at"] = self.state.observed_at.isoformat()
        if self.dictionary_hash is not None:
            details["running_dictionary_hash"] = self.dictionary_hash
            details["identified_by"] = self.identified_by
        return details


def resolve_running_dictionary(
    registry: Registry, vehicle_id: str, now: datetime, config: GateConfig | None = None
) -> Resolution:
    """Apply the resolution rules to the vehicle's latest reading."""
    config = config or registry.config

    if registry.get_vehicle(vehicle_id) is None:
        return Resolution(
            vehicle_id, Reason.UNKNOWN_VEHICLE, f"vehicle '{vehicle_id}' is not in the registry"
        )

    state = registry.get_vehicle_state(vehicle_id)
    if state is None:
        return Resolution(
            vehicle_id,
            Reason.NO_BUILD_IDENTITY,
            f"no build identity has been received from '{vehicle_id}'",
        )

    age_s = (now - state.observed_at).total_seconds()
    if age_s > config.max_state_age_s:
        return Resolution(
            vehicle_id,
            Reason.STALE_BUILD_IDENTITY,
            f"the build identity of '{vehicle_id}' is {age_s:.0f} s old "
            f"(limit {config.max_state_age_s:.0f} s)",
            state=state,
        )

    version = state.observed_project_version
    if is_dirty_version(version) and not config.allow_dirty:
        return Resolution(
            vehicle_id,
            Reason.DIRTY_BUILD,
            f"'{vehicle_id}' reports project version '{version}', a build made from "
            "uncommitted or unreadable source, so no dictionary can be trusted to match it",
            state=state,
        )

    prefix = hash_prefix_from_dictionary_id(state.observed_dictionary_id or "")
    if prefix is not None:
        return _resolve_by_content(registry, vehicle_id, state, prefix)
    return _resolve_by_label(registry, vehicle_id, state)


def _resolve_by_content(
    registry: Registry, vehicle_id: str, state: VehicleState, prefix: str
) -> Resolution:
    matches = registry.dictionaries_by_hash_prefix(prefix)
    if not matches:
        return Resolution(
            vehicle_id,
            Reason.UNKNOWN_BUILD,
            f"'{vehicle_id}' reports dictionary ID '{state.observed_dictionary_id}', "
            "which matches no dictionary in the registry",
            state=state,
        )
    if len(matches) > 1:
        return Resolution(
            vehicle_id,
            Reason.AMBIGUOUS_BUILD,
            f"dictionary ID '{state.observed_dictionary_id}' matches {len(matches)} dictionaries",
            state=state,
        )
    dictionary = matches[0]
    if dictionary.project_version != state.observed_project_version:
        return Resolution(
            vehicle_id,
            Reason.BUILD_IDENTITY_CONFLICT,
            f"'{vehicle_id}' reports project version '{state.observed_project_version}' but its "
            f"dictionary ID points to a dictionary built as '{dictionary.project_version}'",
            state=state,
        )
    return Resolution(
        vehicle_id,
        Reason.OK,
        f"'{vehicle_id}' is running dictionary {dictionary.hash[:12]} "
        f"({dictionary.project_version}), identified by content",
        dictionary_hash=dictionary.hash,
        state=state,
        identified_by="content",
    )


def _resolve_by_label(registry: Registry, vehicle_id: str, state: VehicleState) -> Resolution:
    version = state.observed_project_version
    matches = registry.dictionaries_by_project_version(version)
    if not matches:
        return Resolution(
            vehicle_id,
            Reason.UNKNOWN_BUILD,
            f"'{vehicle_id}' reports project version '{version}', "
            "which matches no dictionary in the registry",
            state=state,
        )
    if len(matches) > 1:
        return Resolution(
            vehicle_id,
            Reason.AMBIGUOUS_BUILD,
            f"project version '{version}' maps to {len(matches)} dictionaries "
            f"({', '.join(d.hash[:12] for d in matches)}) and '{vehicle_id}' does not report "
            "a dictionary ID to tell them apart",
            state=state,
        )
    dictionary = matches[0]
    return Resolution(
        vehicle_id,
        Reason.OK,
        f"'{vehicle_id}' is running dictionary {dictionary.hash[:12]} ({version}), "
        "identified by project version label",
        dictionary_hash=dictionary.hash,
        state=state,
        identified_by="label",
    )
