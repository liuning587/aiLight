"""In-memory ring buffer of recent lightd events."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass


@dataclass
class EventRecord:
    ts: float
    event: str
    phase: str
    session_id: str | None = None
    source: str = "hook"
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class EventLog:
    def __init__(self, maxlen: int = 100) -> None:
        self._maxlen = max(1, maxlen)
        self._entries: deque[EventRecord] = deque(maxlen=self._maxlen)

    def add(
        self,
        event: str,
        phase: str,
        session_id: str | None = None,
        source: str = "hook",
        detail: str = "",
    ) -> None:
        self._entries.appendleft(
            EventRecord(
                ts=time.time(),
                event=event,
                phase=phase,
                session_id=session_id,
                source=source,
                detail=detail,
            )
        )

    def list(self, limit: int = 50) -> list[dict]:
        n = max(1, min(limit, self._maxlen))
        return [e.to_dict() for e in list(self._entries)[:n]]
