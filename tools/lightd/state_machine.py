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

DEFAULT_SESSION = "default"
DEBUG_EXPIRE_SEC = 30.0


@dataclass
class SessionState:
    thinking: bool = False
    busy_count: int = 0
    waiting_count: int = 0
    busy_since: float = 0.0


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
        busy_timeout_sec: float = 120.0,
    ):
        self.done_timeout_sec = done_timeout_sec
        self.waiting_timeout_sec = waiting_timeout_sec
        self.error_display_sec = error_display_sec
        self.busy_timeout_sec = busy_timeout_sec
        self.state = LightState()
        self._sessions: dict[str, SessionState] = {}
        self._waiting_deadline = 0.0
        self._debug_phase: str | None = None
        self._debug_until: float = 0.0

    def reset(self) -> None:
        self._sessions.clear()
        self.state.reset()
        self._waiting_deadline = 0.0
        self._debug_phase = None
        self._debug_until = 0.0

    def session_count(self) -> int:
        return len(self._sessions)

    def _sid(self, session_id: str | None) -> str:
        sid = (session_id or DEFAULT_SESSION).strip() or DEFAULT_SESSION
        return sid

    def _session(self, session_id: str | None) -> SessionState:
        sid = self._sid(session_id)
        if sid not in self._sessions:
            self._sessions[sid] = SessionState()
        return self._sessions[sid]

    def _sync_aggregate(self) -> None:
        if not self._sessions:
            self.state.thinking = False
            self.state.busy_count = 0
            self.state.waiting_count = 0
            return
        self.state.thinking = any(s.thinking for s in self._sessions.values())
        self.state.busy_count = sum(s.busy_count for s in self._sessions.values())
        self.state.waiting_count = sum(s.waiting_count for s in self._sessions.values())

    def resolve_phase(self, now: float | None = None) -> str:
        now = now or time.time()
        if self._debug_until > now and self._debug_phase:
            return self._debug_phase
        return self.state.resolve(now)

    def _set_debug(self, phase: str, now: float) -> None:
        self._debug_phase = phase
        self._debug_until = now + DEBUG_EXPIRE_SEC

    def _clear_debug(self) -> None:
        self._debug_phase = None
        self._debug_until = 0.0

    def _clear_stale_for_event(self, event: str, now: float) -> None:
        """Drop expired timers so the new event wins immediately."""
        if event in ("thinking", "busy", "waiting"):
            self.state.error_until = 0.0
            self.state.done_until = 0.0
        elif event == "user_prompt":
            self.state.error_until = 0.0
            self.state.done_until = 0.0
        elif event == "tool_failure":
            self.state.done_until = 0.0
        elif event in ("tool_start", "session_start"):
            self.state.done_until = 0.0
            if event == "session_start":
                self.state.error_until = 0.0
        elif event == "session_stop":
            self.state.error_until = 0.0
        elif event == "force_idle":
            self.state.error_until = 0.0
            self.state.done_until = 0.0

    def _dec_waiting(self, sess: SessionState, now: float) -> None:
        if sess.waiting_count > 0:
            sess.waiting_count -= 1
        self._sync_aggregate()
        if self.state.waiting_count == 0:
            self._waiting_deadline = 0.0
        elif not self._waiting_deadline:
            self._waiting_deadline = now + self.waiting_timeout_sec

    def _touch(self, event: str) -> None:
        self.state.last_event = event
        self.state.updated_at = time.time()

    def _expire_waiting(self, now: float) -> None:
        if (
            self.state.waiting_count > 0
            and self._waiting_deadline
            and now > self._waiting_deadline
        ):
            for sess in self._sessions.values():
                sess.waiting_count = 0
            self._sync_aggregate()
            self._waiting_deadline = 0.0

    def _expire_busy(self, now: float) -> None:
        changed = False
        for sess in self._sessions.values():
            if (
                sess.busy_count > 0
                and sess.busy_since
                and now - sess.busy_since > self.busy_timeout_sec
            ):
                sess.busy_count = 0
                sess.busy_since = 0.0
                sess.thinking = True
                changed = True
        if changed:
            self._sync_aggregate()

    def apply(
        self, event: str, session_id: str | None = None
    ) -> tuple[str, str | None]:
        """Return (resolved_phase, reason_if_no_change)."""
        now = time.time()
        self._expire_waiting(now)
        self._expire_busy(now)
        if self._debug_until and now >= self._debug_until:
            self._clear_debug()
        # Web debug overlay must not block later test buttons (done / error / idle).
        if event not in ("thinking", "busy", "waiting"):
            self._clear_debug()
        self._clear_stale_for_event(event, now)
        prev = self.resolve_phase(now)
        sid = self._sid(session_id)

        if event == "session_start":
            self._sessions[sid] = SessionState()
            self.state.done_until = 0.0
            self._sync_aggregate()
        elif event == "user_prompt":
            sess = self._session(session_id)
            sess.thinking = True
            self.state.done_until = 0.0
            self._sync_aggregate()
        elif event in ("thinking", "busy", "waiting"):
            self._set_debug(event, now)
        elif event == "tool_start":
            sess = self._session(session_id)
            if sess.waiting_count > 0:
                sess.waiting_count -= 1
            if sess.busy_count == 0:
                sess.busy_since = now
            sess.thinking = False
            sess.busy_count += 1
            self.state.done_until = 0.0
            self._sync_aggregate()
            if self.state.waiting_count > 0 and not self._waiting_deadline:
                self._waiting_deadline = now + self.waiting_timeout_sec
            elif self.state.waiting_count == 0:
                self._waiting_deadline = 0.0
        elif event == "tool_success":
            sess = self._session(session_id)
            if sess.busy_count > 0:
                sess.busy_count -= 1
            if sess.busy_count == 0:
                sess.busy_since = 0.0
                sess.thinking = True
            self._sync_aggregate()
        elif event == "permission_wait":
            sess = self._session(session_id)
            sess.waiting_count += 1
            self._waiting_deadline = now + self.waiting_timeout_sec
            self._sync_aggregate()
        elif event == "permission_done":
            sess = self._session(session_id)
            self._dec_waiting(sess, now)
        elif event == "session_stop":
            if sid in self._sessions:
                del self._sessions[sid]
            self._sync_aggregate()
            if self.state.waiting_count == 0:
                self._waiting_deadline = 0.0
            if (
                self.state.busy_count == 0
                and self.state.waiting_count == 0
                and not self.state.thinking
            ):
                self.state.done_until = now + self.done_timeout_sec
        elif event == "tool_failure":
            sess = self._session(session_id)
            sess.thinking = False
            if sess.busy_count > 0:
                sess.busy_count -= 1
            if sess.busy_count == 0:
                sess.busy_since = 0.0
            self.state.error_until = now + self.error_display_sec
            self._sync_aggregate()
        elif event == "force_idle":
            self.reset()
        else:
            return prev, f"unknown event: {event}"

        self._touch(event)
        new_phase = self.resolve_phase(now)
        if new_phase == prev and event not in (
            "tool_start",
            "permission_wait",
            "permission_done",
            "thinking",
            "busy",
            "waiting",
            "force_idle",
            "session_stop",
            "session_start",
            "user_prompt",
            "tool_failure",
        ):
            return new_phase, "no_change"
        return new_phase, None

    def tick(self) -> str | None:
        """Return new phase if timeout changed visible state, else None."""
        now = time.time()
        prev = self.resolve_phase(now)
        self._expire_waiting(now)
        self._expire_busy(now)
        if self._debug_until and now >= self._debug_until:
            self._clear_debug()
        if self.state.done_until and now >= self.state.done_until:
            self.state.done_until = 0.0
        if self.state.error_until and now >= self.state.error_until:
            self.state.error_until = 0.0
        new_phase = self.resolve_phase(now)
        if new_phase != prev:
            return new_phase
        return None
