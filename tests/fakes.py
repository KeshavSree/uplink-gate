"""Stand-ins for a live vehicle link, so unit tests need no network and no Docker."""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from gate.registry import Registry, utcnow
from gate.telemetry import ChannelReading, EventReading, LinkDown

SEQUENCER = "Ref.fpySeq"


class FakeComms:
    """A `VehicleComms` whose vehicle answers the way the demo flight software does."""

    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        #: vehicle ID -> (project version, dictionary ID) the vehicle would report if asked.
        self.identity: dict[str, tuple[str, str | None]] = {}
        self.sent_files: list[tuple[str, Path, str]] = []
        self.sent_commands: list[tuple[str, str, list[str]]] = []
        self.answers_identity = True
        self.link_up = True
        self.confirms_file = True
        #: What the sequencer reports after RUN: "done", "error" or "silent".
        self.run_outcome = "done"
        #: Called just before an identity request is answered. Lets a test reflash mid-uplink.
        self.before_identity: Callable[[str], None] | None = None

    def refresh_identity(self, vehicle_id: str, timeout_s: float) -> bool:
        if not self.link_up:
            raise LinkDown(f"no live connection to '{vehicle_id}'")
        if self.before_identity is not None:
            self.before_identity(vehicle_id)
        if not self.answers_identity or vehicle_id not in self.identity:
            return False
        version, dictionary_id = self.identity[vehicle_id]
        self.registry.set_vehicle_state(
            vehicle_id,
            version,
            observed_at=utcnow(),
            dictionary_id=dictionary_id,
            source="telemetry",
        )
        return True

    def uplink_file(self, vehicle_id: str, local_path: Path, remote_path: str) -> None:
        if not self.link_up:
            raise LinkDown(f"no live connection to '{vehicle_id}'")
        self.sent_files.append((vehicle_id, Path(local_path), remote_path))

    def send_command(self, vehicle_id: str, name: str, args: Sequence[str]) -> None:
        self.sent_commands.append((vehicle_id, name, list(args)))

    def wait_for_event(
        self,
        vehicle_id: str,
        names: Collection[str] | None,
        since: datetime,
        timeout_s: float,
        match: Callable[[EventReading], bool] | None = None,
    ) -> EventReading | None:
        candidates: list[EventReading] = []
        if self.sent_files and self.confirms_file:
            remote = self.sent_files[-1][2]
            candidates.append(
                self._event(
                    vehicle_id,
                    "FileHandling.fileUplink.FileReceived",
                    "ACTIVITY_HI",
                    f"Received file {remote}",
                )
            )
        if self.sent_commands:
            remote = self.sent_commands[-1][2][0]
            if self.run_outcome == "done":
                candidates.append(
                    self._event(
                        vehicle_id,
                        f"{SEQUENCER}.SequenceDone",
                        "ACTIVITY_HI",
                        f"Completed sequence file {remote}",
                    )
                )
            elif self.run_outcome == "error":
                candidates.append(
                    self._event(
                        vehicle_id,
                        f"{SEQUENCER}.SequenceExitedWithError",
                        "WARNING_HI",
                        f"Sequence {remote} exited with error code 17",
                    )
                )
        for event in candidates:
            if (names is None or event.name in names) and (match is None or match(event)):
                return event
        return None

    @staticmethod
    def _event(vehicle_id: str, name: str, severity: str, message: str) -> EventReading:
        return EventReading(vehicle_id, name, severity, message, utcnow())


class FakeLink:
    """Replaces `GdsLink` inside a `VehicleIngest`. Tests push readings through `feed_*`."""

    instances: list[FakeLink] = []
    fail_connect = False

    def __init__(
        self,
        vehicle_id: str,
        endpoint: str,
        dictionary_path: Any,
        file_store: Path,
        on_channel: Callable[[ChannelReading], None],
        on_event: Callable[[EventReading], None],
    ) -> None:
        self.vehicle_id = vehicle_id
        self.endpoint = endpoint
        self.dictionary_path = str(dictionary_path)
        self._on_channel = on_channel
        self._on_event = on_event
        self.alive = False
        self.commands: list[tuple[str, list[str]]] = []
        self.files: list[tuple[Path, str]] = []
        #: What the vehicle reports when it is sent the version command.
        self.reports: tuple[str, str | None] | None = None
        FakeLink.instances.append(self)

    def connect(self) -> None:
        if FakeLink.fail_connect:
            raise ConnectionRefusedError("connection refused")
        self.alive = True

    def close(self) -> None:
        self.alive = False

    def send_command(self, name: str, args: Sequence[str]) -> None:
        self.commands.append((name, list(args)))
        if name.endswith(".VERSION") and self.reports is not None:
            version, dictionary_id = self.reports
            self.feed_channel("CdhCore.version.ProjectVersion", version)
            if dictionary_id is not None:
                self.feed_channel(
                    "CdhCore.version.CustomVersion01",
                    {"version_enum": "PROJECT_VERSION_00", "version_value": dictionary_id},
                )

    def uplink_file(self, local_path: Path, remote_path: str) -> None:
        self.files.append((Path(local_path), remote_path))

    def feed_channel(self, name: str, value: Any, at: datetime | None = None) -> None:
        self._on_channel(ChannelReading(self.vehicle_id, name, value, at or utcnow()))

    def feed_event(self, name: str, severity: str, message: str) -> None:
        self._on_event(EventReading(self.vehicle_id, name, severity, message, utcnow()))
