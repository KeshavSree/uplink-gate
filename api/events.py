"""In-process event bus feeding the WebSocket. Safe to publish to from any thread."""

from __future__ import annotations

import asyncio
import itertools
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any


class EventBus:
    def __init__(self, history: int = 500) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._recent: deque[dict[str, Any]] = deque(maxlen=history)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record an event and deliver it to every WebSocket subscriber."""
        with self._lock:
            event = {
                "seq": next(self._ids),
                "type": kind,
                "at": datetime.now(UTC).isoformat(),
                **(payload or {}),
            }
            self._recent.append(event)
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._fan_out, event)
        return event

    def _fan_out(self, event: dict[str, Any]) -> None:
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()  # a slow client loses its oldest event, not the newest
            queue.put_nowait(event)

    def recent(self, limit: int = 100, kinds: set[str] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            events = [e for e in self._recent if kinds is None or e["type"] in kinds]
        return events[-limit:]

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)
