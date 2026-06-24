"""Tests for per-session state machine aggregation."""

import time

from tools.lightd.state_machine import (
    STATE_BUSY,
    STATE_DONE,
    STATE_ERROR,
    STATE_THINKING,
    STATE_WAITING,
    StateMachine,
)


def test_session_stop_does_not_clear_other_sessions():
    sm = StateMachine(done_timeout_sec=60)
    sm.apply("tool_start", session_id="a")
    sm.apply("tool_start", session_id="b")
    assert sm.state.busy_count == 2

    sm.apply("session_stop", session_id="a")
    assert sm.state.busy_count == 1
    assert sm.resolve_phase() == STATE_BUSY


def test_session_stop_sets_done_when_all_idle():
    sm = StateMachine(done_timeout_sec=60)
    sm.apply("user_prompt", session_id="a")
    sm.apply("session_stop", session_id="a")
    assert sm.resolve_phase() == STATE_DONE


def test_tool_start_clears_waiting_for_session():
    sm = StateMachine()
    sm.apply("permission_wait", session_id="a")
    assert sm.resolve_phase() == STATE_WAITING
    sm.apply("tool_start", session_id="a")
    assert sm.state.waiting_count == 0
    assert sm.state.busy_count == 1


def test_trae_permission_done_via_post_tool_path():
    sm = StateMachine()
    sm.apply("permission_wait", session_id="a")
    sm.apply("permission_done", session_id="a")
    assert sm.state.waiting_count == 0


def test_web_debug_overlay_keeps_sessions():
    sm = StateMachine()
    sm.apply("tool_start", session_id="live")
    assert sm.state.busy_count == 1
    sm.apply("thinking")
    assert sm.resolve_phase() == STATE_THINKING
    assert sm.state.busy_count == 1


def test_web_debug_session_stop_clears_overlay():
    sm = StateMachine(done_timeout_sec=60)
    sm.apply("busy")
    assert sm.resolve_phase() == STATE_BUSY
    sm.apply("session_stop")
    assert sm.resolve_phase() == STATE_DONE


def test_web_debug_tool_failure_clears_overlay():
    sm = StateMachine()
    sm.apply("busy")
    assert sm.resolve_phase() == STATE_BUSY
    sm.apply("tool_failure")
    assert sm.resolve_phase() == STATE_ERROR


def test_error_then_session_stop_shows_done():
    sm = StateMachine(done_timeout_sec=60)
    sm.apply("tool_failure")
    assert sm.resolve_phase() == STATE_ERROR
    sm.apply("session_stop")
    assert sm.resolve_phase() == STATE_DONE


def test_done_then_tool_failure_shows_error():
    sm = StateMachine(done_timeout_sec=60)
    sm.apply("session_stop")
    assert sm.resolve_phase() == STATE_DONE
    phase, reason = sm.apply("tool_failure")
    assert reason is None
    assert phase == STATE_ERROR


def test_tool_failure_resends_while_already_error():
    sm = StateMachine()
    sm.apply("tool_failure")
    phase, reason = sm.apply("tool_failure")
    assert reason is None
    assert phase == STATE_ERROR


def test_user_prompt_after_error_shows_thinking():
    sm = StateMachine()
    sm.apply("tool_failure")
    sm.apply("user_prompt", session_id="a")
    assert sm.resolve_phase() == STATE_THINKING


def test_busy_timeout_clears_stale_busy():
    sm = StateMachine(busy_timeout_sec=1)
    sm.apply("tool_start", session_id="a")
    assert sm.resolve_phase() == STATE_BUSY
    sm._sessions["a"].busy_since = time.time() - 5
    phase = sm.tick()
    assert phase == STATE_THINKING
    assert sm.state.busy_count == 0
