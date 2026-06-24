"""PromLight-style state aggregation for aiLight daemon."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


STATE_IDLE = "idle"
STATE_THINKING = "thinking"
STATE_BUSY = "busy"
STATE_WAITING = "waiting"
STATE_DONE = "done"
STATE_ERROR = "error"

# Higher number wins when multiple conditions are active.
_PRIORITY = {
    STATE_ERROR: 50,
    STATE_WAITING: 40,
    STATE_BUSY: 30,
    STATE_THINKING: 20,
    STATE_DONE: 10,
    STATE_IDLE: 0,
}


@dataclass
class LightState:
    thinking: bool = False
    busy_count: int = 0
    waiting_count: int = 0
    done_until: float = 0.0
    error_until: float = 0.0
    last_event: str = ""
    last_command: str = ""
    updated_at: float = field(default_factory=time.time)

    def reset(self) -> None:
        self.thinking = False
        self.busy_count = 0
        self.waiting_count = 0
        self.done_until = 0.0
        self.error_until = 0.0
        self.updated_at = time.time()

    def resolve(self, now: float | None = None) -> str:
        now = now or time.time()
        self.updated_at = now
        candidates: list[str] = [STATE_IDLE]
        if self.error_until > now:
            candidates.append(STATE_ERROR)
        if self.waiting_count > 0:
            candidates.append(STATE_WAITING)
        if self.busy_count > 0:
            candidates.append(STATE_BUSY)
        if self.thinking:
            candidates.append(STATE_THINKING)
        if self.done_until > now:
            candidates.append(STATE_DONE)
        return max(candidates, key=lambda s: _PRIORITY[s])

    def to_dict(self) -> dict:
        phase = self.resolve()
        return {
            "phase": phase,
            "thinking": self.thinking,
            "busy_count": self.busy_count,
            "waiting_count": self.waiting_count,
            "done_until": self.done_until,
            "error_until": self.error_until,
            "last_event": self.last_event,
            "last_command": self.last_command,
            "updated_at": self.updated_at,
        }


class StateMachine:
    def __init__(
        self,
        done_timeout_sec: float = 60.0,
        waiting_timeout_sec: float = 300.0,
        error_display_sec: float = 4.0,
    ):
        self.done_timeout_sec = done_timeout_sec
        self.waiting_timeout_sec = waiting_timeout_sec
        self.error_display_sec = error_display_sec
        self.state = LightState()
        self._waiting_deadline = 0.0

    def _touch(self, event: str) -> None:
        self.state.last_event = event
        self.state.updated_at = time.time()

    def _expire_waiting(self, now: float) -> None:
        if (
            self.state.waiting_count > 0
            and self._waiting_deadline
            and now > self._waiting_deadline
        ):
            self.state.waiting_count = 0
            self._waiting_deadline = 0.0

    def apply(self, event: str) -> tuple[str, str | None]:
        """Return (resolved_phase, reason_if_no_change)."""
        now = time.time()
        self._expire_waiting(now)
        prev = self.state.resolve(now)

        if event == "session_start":
            self.state.reset()
        elif event == "user_prompt":
            self.state.thinking = True
            self.state.done_until = 0.0
        elif event == "thinking":
            # Web console manual test button
            self.state.reset()
            self.state.thinking = True
        elif event == "busy":
            # Web console manual test button
            self.state.reset()
            self.state.busy_count = 1
        elif event == "waiting":
            # Web console manual test button
            self.state.reset()
            self.state.waiting_count = 1
            self._waiting_deadline = now + self.waiting_timeout_sec
        elif event == "tool_start":
            self.state.thinking = False
            self.state.busy_count += 1
            self.state.done_until = 0.0
        elif event == "tool_success":
            if self.state.busy_count > 0:
                self.state.busy_count -= 1
            if self.state.busy_count == 0:
                self.state.thinking = True
        elif event == "permission_wait":
            self.state.waiting_count += 1
            self._waiting_deadline = now + self.waiting_timeout_sec
        elif event == "permission_done":
            if self.state.waiting_count > 0:
                self.state.waiting_count -= 1
            if self.state.waiting_count == 0:
                self._waiting_deadline = 0.0
        elif event == "session_stop":
            self.state.thinking = False
            self.state.busy_count = 0
            self.state.waiting_count = 0
            self._waiting_deadline = 0.0
            self.state.done_until = now + self.done_timeout_sec
        elif event == "tool_failure":
            self.state.thinking = False
            if self.state.busy_count > 0:
                self.state.busy_count -= 1
            self.state.error_until = now + self.error_display_sec
        elif event == "force_idle":
            self.state.reset()
        else:
            return prev, f"unknown event: {event}"

        self._touch(event)
        new_phase = self.state.resolve(now)
        if new_phase == prev and event not in (
            "tool_start",
            "permission_wait",
            "thinking",
            "busy",
            "waiting",
            "force_idle",
        ):
            return new_phase, "no_change"
        return new_phase, None

    def tick(self) -> str | None:
        """Return new phase if timeout changed visible state, else None."""
        now = time.time()
        prev = self.state.resolve(now)
        self._expire_waiting(now)
        if self.state.done_until and now >= self.state.done_until:
            self.state.done_until = 0.0
        if self.state.error_until and now >= self.state.error_until:
            self.state.error_until = 0.0
        new_phase = self.state.resolve(now)
        if new_phase != prev:
            return new_phase
        return None
