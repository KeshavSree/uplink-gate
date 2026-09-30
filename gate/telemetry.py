"""Live telemetry: learn what each vehicle is running, and talk to it.

One `VehicleIngest` thread per vehicle connects to that vehicle's ground data system (GDS),
decodes its telemetry and events, and keeps the registry's `vehicle_state` row current.
`IngestManager` owns the threads and is the `VehicleComms` implementation the uplink
adapter uses to reach a vehicle.

fprime-gds is imported only inside `GdsLink`, so the rest of the gate (and its unit tests)
does not need it installed.
"""

from __future__ import annotations

import logging
import select
import threading
import time
from collections import deque
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from gate.config import GateConfig
from gate.registry import Dictionary, Registry, utcnow
from gate.resolve import resolve_running_dictionary

log = logging.getLogger(__name__)

#: Two version readings this close together belong to the same report from the vehicle.
IDENTITY_GROUP_WINDOW = timedelta(seconds=5)


@dataclass(frozen=True)
class ChannelReading:
    """One decoded telemetry channel value."""

    vehicle_id: str
    name: str
    value: Any
    received_at: datetime


@dataclass(frozen=True)
class EventReading:
    """One decoded flight software event."""

    vehicle_id: str
    name: str
    severity: str
    message: str
    received_at: datetime
    args: tuple[Any, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "vehicle_id": self.vehicle_id,
            "name": self.name,
            "severity": self.severity,
            "message": self.message,
            "received_at": self.received_at.isoformat(),
        }


class VehicleComms(Protocol):
    """What the uplink adapter needs from the link to a vehicle."""

    def refresh_identity(self, vehicle_id: str, timeout_s: float) -> bool:
        """Ask the vehicle for its versions and wait for a fresh reading."""

    def uplink_file(self, vehicle_id: str, local_path: Path, remote_path: str) -> None: ...

    def send_command(self, vehicle_id: str, name: str, args: Sequence[str]) -> None: ...

    def wait_for_event(
        self,
        vehicle_id: str,
        names: Collection[str] | None,
        since: datetime,
        timeout_s: float,
        match: Callable[[EventReading], bool] | None = None,
    ) -> EventReading | None:
        """Return the first event after `since` that has one of `names` (any name if None)
        and satisfies `match`, or None on timeout."""


Publish = Callable[[str, dict[str, Any]], Any]


class ChannelSink(Protocol):
    """Somewhere to store every channel reading, for example InfluxDB."""

    def write(self, reading: ChannelReading) -> None: ...


class IdentityTracker:
    """Turns version telemetry into the registry's build identity reading for one vehicle.

    The project version and the dictionary ID arrive as two separate channel values. They
    are paired only when they were received within a few seconds of each other, so a
    dictionary ID left over from before a reflash is never combined with a new project
    version.
    """

    def __init__(self, registry: Registry, vehicle_id: str, config: GateConfig) -> None:
        self.registry = registry
        self.vehicle_id = vehicle_id
        self.config = config
        self._project_version: str | None = None
        self._project_version_at: datetime | None = None
        self._dictionary_id: str | None = None
        self._dictionary_id_at: datetime | None = None

    def on_channel(self, reading: ChannelReading) -> bool:
        """Feed one reading. Returns True if it updated the build identity."""
        if reading.name == self.config.project_version_channel:
            self._project_version = str(reading.value)
            self._project_version_at = reading.received_at
        elif reading.name == self.config.dictionary_id_channel:
            value = reading.value
            if isinstance(value, dict):
                value = value.get("version_value")
            self._dictionary_id = str(value) if value is not None else None
            self._dictionary_id_at = reading.received_at
        else:
            return False
        return self._commit()

    def _commit(self) -> bool:
        if self._project_version is None or self._project_version_at is None:
            return False
        dictionary_id = None
        observed_at = self._project_version_at
        if (
            self._dictionary_id_at is not None
            and abs(self._dictionary_id_at - self._project_version_at) <= IDENTITY_GROUP_WINDOW
        ):
            dictionary_id = self._dictionary_id
            observed_at = max(observed_at, self._dictionary_id_at)
        self.registry.set_vehicle_state(
            self.vehicle_id,
            self._project_version,
            observed_at=observed_at,
            dictionary_id=dictionary_id,
            source="telemetry",
        )
        resolution = resolve_running_dictionary(
            self.registry, self.vehicle_id, observed_at, self.config
        )
        self.registry.set_resolved_dictionary(self.vehicle_id, resolution.dictionary_hash)
        return True


class GdsLink:
    """A connection to one vehicle's GDS, through fprime-gds's standard pipeline.

    The GDS relays raw F Prime packets. Decoding them into named channels and events
    happens here, with the dictionary given to the constructor.
    """

    def __init__(
        self,
        vehicle_id: str,
        endpoint: str,
        dictionary_path: Path | str,
        file_store: Path,
        on_channel: Callable[[ChannelReading], None],
        on_event: Callable[[EventReading], None],
    ) -> None:
        self.vehicle_id = vehicle_id
        self.endpoint = endpoint
        self.dictionary_path = str(dictionary_path)
        self.file_store = file_store
        self._on_channel = on_channel
        self._on_event = on_event
        self._pipeline: Any = None
        self._eof = threading.Event()
        self._send_lock = threading.Lock()

    @property
    def alive(self) -> bool:
        return self._pipeline is not None and not self._eof.is_set()

    def connect(self) -> None:
        from fprime_gds.common.handlers import DataHandler
        from fprime_gds.common.models.dictionaries import Dictionaries
        from fprime_gds.common.pipeline.standard import StandardPipeline
        from fprime_gds.common.transport import ThreadedTCPSocketClient
        from fprime_gds.common.utils.config_manager import ConfigManager

        link = self

        class Client(ThreadedTCPSocketClient):  # type: ignore[misc]
            """The stock client spins forever on a closed socket. This one notices and stops."""

            def recv(self, timeout: float = 0.1) -> bytes:
                ready = select.select([self.sock], [], [], timeout)
                if not ready[0]:
                    return b""
                try:
                    data: bytes = self.sock.recv(self.chunk_size)
                except OSError:
                    data = b""
                if data == b"":
                    link._eof.set()
                    self.stop()
                return data

        class Channels(DataHandler):  # type: ignore[misc]
            def data_callback(self, data: Any, sender: Any = None) -> None:
                link._on_channel(
                    ChannelReading(
                        link.vehicle_id, data.template.get_full_name(), data.get_val(), utcnow()
                    )
                )

        class Events(DataHandler):  # type: ignore[misc]
            def data_callback(self, data: Any, sender: Any = None) -> None:
                args = tuple(getattr(a, "val", a) for a in (data.args or ()))
                link._on_event(
                    EventReading(
                        link.vehicle_id,
                        data.template.get_full_name(),
                        str(data.get_severity()).removeprefix("EventSeverity."),
                        str(data.get_display_text()),
                        utcnow(),
                        args,
                    )
                )

        # NOTE: fprime-gds keeps type widths in one process-wide ConfigManager. Every
        # dictionary loaded here writes into it. The demo builds agree on those widths.
        dictionaries = Dictionaries.load_dictionaries_into_config(self.dictionary_path)
        self.file_store.mkdir(parents=True, exist_ok=True)
        pipeline = StandardPipeline()
        pipeline.transport_implementation = Client
        pipeline.setup(
            ConfigManager.get_instance(),
            dictionaries,
            str(self.file_store),
            logging_prefix=None,
            data_logging_enabled=False,
        )
        # The stock receive timeout is 100 s, which is also how long a disconnect would take.
        pipeline.client_socket.timeout = 0.5
        pipeline.coders.register_channel_consumer(Channels())
        pipeline.coders.register_event_consumer(Events())
        try:
            pipeline.connect(self.endpoint)
        except Exception:
            try:
                pipeline.disconnect()
            finally:
                raise
        self._pipeline = pipeline

    def close(self) -> None:
        pipeline, self._pipeline = self._pipeline, None
        if pipeline is not None:
            try:
                pipeline.disconnect()
            except Exception:  # closing a dead link must never take the ingest down
                log.debug("error while closing link to %s", self.vehicle_id, exc_info=True)

    def send_command(self, name: str, args: Sequence[str]) -> None:
        with self._send_lock:
            self._pipeline.send_command(name, list(args))

    def uplink_file(self, local_path: Path, remote_path: str) -> None:
        with self._send_lock:
            self._pipeline.uplink_file(str(local_path), remote_path)


LinkFactory = Callable[..., Any]


class LinkDown(RuntimeError):
    """There is no live connection to the vehicle's GDS."""


class VehicleIngest:
    """Keeps a link to one vehicle up and turns what arrives into registry state and events."""

    #: How long a command or uplink waits for the link to come back before giving up.
    LINK_WAIT_S = 5.0

    def __init__(
        self,
        registry: Registry,
        vehicle_id: str,
        endpoint: str,
        config: GateConfig | None = None,
        publish: Publish | None = None,
        sink: ChannelSink | None = None,
        link_factory: LinkFactory = GdsLink,
        on_event: Callable[[EventReading], None] | None = None,
    ) -> None:
        self.registry = registry
        self.vehicle_id = vehicle_id
        self.endpoint = endpoint
        self.config = config or registry.config
        self._publish = publish or (lambda kind, payload: None)
        self._sink = sink
        self._link_factory = link_factory
        self._on_event_hook = on_event
        self._tracker = IdentityTracker(registry, vehicle_id, self.config)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"ingest-{vehicle_id}", daemon=True)
        self._lock = threading.Lock()
        self._link: Any = None
        self._events: deque[EventReading] = deque(maxlen=1000)
        self._changed = threading.Condition()
        self._decode_dictionary_hash: str | None = None
        self._last_data_at: datetime | None = None
        self._error: str | None = None
        self._channels_received = 0

    # --- lifecycle ----------------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self._link is not None,
                "endpoint": self.endpoint,
                "decode_dictionary_hash": self._decode_dictionary_hash,
                "last_data_at": self._last_data_at.isoformat() if self._last_data_at else None,
                "channels_received": self._channels_received,
                "error": self._error,
            }

    def _choose_decode_dictionary(self, current: Dictionary | None) -> Dictionary | None:
        """The dictionary to decode this vehicle's telemetry with.

        Prefer the one the vehicle resolved to. Before that is known, any registered
        dictionary will do to read the version channels, as long as those channels keep the
        same ID and type across builds. That is the one thing every build must agree on.
        """
        state = self.registry.get_vehicle_state(self.vehicle_id)
        if state is not None and state.resolved_dictionary_hash:
            resolved = self.registry.get_dictionary(state.resolved_dictionary_hash)
            if resolved is not None:
                return resolved
        if current is not None:
            return current
        dictionaries = self.registry.list_dictionaries()
        return dictionaries[0] if dictionaries else None

    def _run(self) -> None:
        dictionary: Dictionary | None = None
        while not self._stop.is_set():
            dictionary = self._choose_decode_dictionary(dictionary)
            if dictionary is None:
                self._set_error("no dictionary in the registry to decode telemetry with")
                self._stop.wait(2.0)
                continue

            link = self._link_factory(
                self.vehicle_id,
                self.endpoint,
                dictionary.path,
                self.config.gds_store / self.vehicle_id,
                self._on_channel,
                self._on_event,
            )
            try:
                link.connect()
            except Exception as error:
                self._set_error(f"cannot connect to {self.endpoint}: {error}")
                self._stop.wait(2.0)
                continue

            with self._lock:
                self._link = link
                self._decode_dictionary_hash = dictionary.hash
                self._error = None
            self._publish("vehicle.link", {"vehicle_id": self.vehicle_id, "connected": True})
            log.info("%s: connected to %s", self.vehicle_id, self.endpoint)

            last_poll = 0.0
            while not self._stop.is_set() and link.alive:
                if time.monotonic() - last_poll >= self.config.identity_poll_s:
                    self._request_identity(link)
                    last_poll = time.monotonic()
                self._stop.wait(0.5)
                wanted = self._choose_decode_dictionary(dictionary)
                if wanted is not None and wanted.hash != dictionary.hash:
                    log.info("%s: switching decode dictionary to %s", self.vehicle_id, wanted.hash)
                    dictionary = wanted
                    break

            with self._lock:
                self._link = None
            link.close()
            self._publish("vehicle.link", {"vehicle_id": self.vehicle_id, "connected": False})

    def _set_error(self, message: str) -> None:
        with self._lock:
            changed = self._error != message
            self._error = message
        if changed:
            log.warning("%s: %s", self.vehicle_id, message)

    def _request_identity(self, link: Any) -> None:
        try:
            link.send_command(self.config.version_command, self.config.version_command_args)
        except Exception as error:
            log.warning("%s: could not request versions: %s", self.vehicle_id, error)

    # --- data from the vehicle ------------------------------------------------------------

    def _on_channel(self, reading: ChannelReading) -> None:
        with self._lock:
            self._last_data_at = reading.received_at
            self._channels_received += 1
        if self._sink is not None:
            self._sink.write(reading)
        if self._tracker.on_channel(reading):
            with self._changed:
                self._changed.notify_all()
            self._publish("vehicle.state", {"vehicle_id": self.vehicle_id})

    def _on_event(self, reading: EventReading) -> None:
        with self._changed:
            self._events.append(reading)
            self._changed.notify_all()
        if self._on_event_hook is not None:
            self._on_event_hook(reading)
        self._publish("vehicle.event", reading.to_dict())

    # --- used by the uplink adapter -------------------------------------------------------

    def _require_link(self) -> Any:
        """The live link. Waits briefly, because the ingest reconnects when the vehicle
        changes build (to decode with the new dictionary) and that takes a moment."""
        deadline = time.monotonic() + self.LINK_WAIT_S
        while True:
            with self._lock:
                link = self._link
            if link is not None and link.alive:
                return link
            if time.monotonic() >= deadline or self._stop.is_set():
                raise LinkDown(f"no live connection to '{self.vehicle_id}' at {self.endpoint}")
            time.sleep(0.1)

    def refresh_identity(self, timeout_s: float) -> bool:
        asked_at = utcnow()
        self._request_identity(self._require_link())
        deadline = time.monotonic() + timeout_s
        with self._changed:
            while True:
                state = self.registry.get_vehicle_state(self.vehicle_id)
                if state is not None and state.observed_at >= asked_at:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._changed.wait(min(remaining, 0.5))

    def uplink_file(self, local_path: Path, remote_path: str) -> None:
        self._require_link().uplink_file(local_path, remote_path)

    def send_command(self, name: str, args: Sequence[str]) -> None:
        self._require_link().send_command(name, args)

    def wait_for_event(
        self,
        names: Collection[str] | None,
        since: datetime,
        timeout_s: float,
        match: Callable[[EventReading], bool] | None = None,
    ) -> EventReading | None:
        deadline = time.monotonic() + timeout_s
        with self._changed:
            while True:
                for event in self._events:
                    if (
                        event.received_at >= since
                        and (names is None or event.name in names)
                        and (match is None or match(event))
                    ):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._changed.wait(min(remaining, 0.5))

    def recent_events(self, limit: int = 100) -> list[EventReading]:
        with self._changed:
            return list(self._events)[-limit:]


class IngestManager:
    """Runs one ingest per vehicle that has a GDS endpoint. Implements `VehicleComms`."""

    def __init__(
        self,
        registry: Registry,
        config: GateConfig | None = None,
        publish: Publish | None = None,
        sink: ChannelSink | None = None,
        link_factory: LinkFactory = GdsLink,
        on_event: Callable[[EventReading], None] | None = None,
    ) -> None:
        self.registry = registry
        self.config = config or registry.config
        self._publish = publish
        self._sink = sink
        self._link_factory = link_factory
        self._on_event = on_event
        self._ingests: dict[str, VehicleIngest] = {}

    def start(self) -> None:
        for vehicle in self.registry.list_vehicles():
            if vehicle.gds_endpoint and vehicle.id not in self._ingests:
                ingest = VehicleIngest(
                    self.registry,
                    vehicle.id,
                    vehicle.gds_endpoint,
                    self.config,
                    self._publish,
                    self._sink,
                    self._link_factory,
                    self._on_event,
                )
                self._ingests[vehicle.id] = ingest
                ingest.start()

    def stop(self) -> None:
        for ingest in self._ingests.values():
            ingest.stop()
        self._ingests.clear()

    def status(self, vehicle_id: str) -> dict[str, Any] | None:
        ingest = self._ingests.get(vehicle_id)
        return ingest.status() if ingest else None

    def recent_events(self, vehicle_id: str, limit: int = 100) -> list[EventReading]:
        ingest = self._ingests.get(vehicle_id)
        return ingest.recent_events(limit) if ingest else []

    def _ingest(self, vehicle_id: str) -> VehicleIngest:
        ingest = self._ingests.get(vehicle_id)
        if ingest is None:
            raise LinkDown(f"'{vehicle_id}' has no GDS endpoint, so there is no link to it")
        return ingest

    def refresh_identity(self, vehicle_id: str, timeout_s: float) -> bool:
        return self._ingest(vehicle_id).refresh_identity(timeout_s)

    def uplink_file(self, vehicle_id: str, local_path: Path, remote_path: str) -> None:
        self._ingest(vehicle_id).uplink_file(local_path, remote_path)

    def send_command(self, vehicle_id: str, name: str, args: Sequence[str]) -> None:
        self._ingest(vehicle_id).send_command(name, args)

    def wait_for_event(
        self,
        vehicle_id: str,
        names: Collection[str] | None,
        since: datetime,
        timeout_s: float,
        match: Callable[[EventReading], bool] | None = None,
    ) -> EventReading | None:
        return self._ingest(vehicle_id).wait_for_event(names, since, timeout_s, match)
