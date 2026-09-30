"""Record testbed runs from the Fpy sequencer's own completion events.

The uplink adapter stores bytecode on a vehicle as `<first 16 hex chars of its SHA-256>.bin`.
The sequencer's events name the file it ran, so a completion event can be tied back to the
exact bytecode without trusting whoever started the run.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from gate.config import GateConfig
from gate.registry import Registry, TestbedRun
from gate.telemetry import EventReading

FAILURE_SEVERITIES = {"WARNING_HI", "WARNING_LO", "FATAL"}
_UPLINKED_FILE = re.compile(r"/([0-9a-f]{16})\.bin\b")


class TestbedRecorder:
    """Feed it every vehicle event. It writes a `testbed_run` row when a sequence ends."""

    __test__ = False  # not a pytest class

    def __init__(
        self,
        registry: Registry,
        config: GateConfig | None = None,
        publish: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.registry = registry
        self.config = config or registry.config
        self._publish = publish
        #: (vehicle, bytecode prefix) -> time of the first event of the run in progress.
        self._started: dict[tuple[str, str], datetime] = {}

    def on_event(self, event: EventReading) -> TestbedRun | None:
        sequencer = self.config.sequencer + "."
        if not event.name.startswith(sequencer):
            return None
        match = _UPLINKED_FILE.search(event.message)
        if match is None:
            return None
        prefix = match.group(1)
        key = (event.vehicle_id, prefix)
        self._started.setdefault(key, event.received_at)

        if event.name == sequencer + "SequenceDone":
            result = "SUCCESS"
        elif event.severity in FAILURE_SEVERITIES:
            result = "FAIL"
        else:
            return None
        started_at = self._started.pop(key)

        vehicle = self.registry.get_vehicle(event.vehicle_id)
        if vehicle is None or vehicle.kind != "testbed":
            return None
        bytecode_sha256 = self.registry.bytecode_sha256_by_prefix(prefix)
        state = self.registry.get_vehicle_state(event.vehicle_id)
        if bytecode_sha256 is None or state is None or not state.resolved_dictionary_hash:
            return None

        run = self.registry.add_testbed_run(
            TestbedRun(
                bytecode_sha256=bytecode_sha256,
                dictionary_hash=state.resolved_dictionary_hash,
                vehicle_id=event.vehicle_id,
                result=result,
                started_at=started_at,
                finished_at=event.received_at,
                evidence={"event": event.name, "message": event.message},
            )
        )
        if self._publish is not None:
            self._publish("testbed.run", {"run": run.to_dict()})
        return run
