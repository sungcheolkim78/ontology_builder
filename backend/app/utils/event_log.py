"""In-memory ring buffer of recent backend activity (one line per inbound
request: received, then its result or error), polled by the frontend's
Settings view as a lightweight "backend terminal log". Deliberately not
persisted to disk or LadybugDB -- a backend restart clearing the log is fine,
since this is a live-activity view for local debugging, not an audit trail
(Langfuse, wired up separately in app.llm.telemetry, is the durable record of
LLM calls specifically)."""

from __future__ import annotations

import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Literal, TypedDict

MAX_EVENTS = 100

# Regenerated on every process start, and returned alongside get_events() by
# main.py's /api/events route -- lets a long-lived frontend session notice a
# backend restart (routine here, see CLAUDE.md's podman-compose down/up
# gotcha), whose event ids restart from 1, rather than mistaking its own
# stale, much larger last-seen id for "nothing new yet" forever.
BOOT_ID = uuid.uuid4().hex

EventKind = Literal["command", "result", "error"]


class Event(TypedDict):
    id: int
    timestamp: str
    kind: EventKind
    message: str


_lock = threading.Lock()
_events: deque[Event] = deque(maxlen=MAX_EVENTS)
_next_id = 1


def log_event(kind: EventKind, message: str) -> None:
    global _next_id
    with _lock:
        _events.append(
            {
                "id": _next_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "kind": kind,
                "message": message,
            }
        )
        _next_id += 1


def get_events(since: int = 0) -> list[Event]:
    """Events with id > since, oldest first. since=0 (the default) returns
    every event still in the buffer. A since older than the buffer's oldest
    remaining entry (already evicted by MAX_EVENTS) still just returns
    everything currently held rather than erroring -- the frontend only ever
    passes back an id it actually received, so this only happens right after
    a backend restart resets the id sequence."""
    with _lock:
        return [event for event in _events if event["id"] > since]


def clear_events() -> None:
    """Test-only reset -- the buffer is global, module-level state shared
    across the whole test process."""
    global _next_id
    with _lock:
        _events.clear()
        _next_id = 1
