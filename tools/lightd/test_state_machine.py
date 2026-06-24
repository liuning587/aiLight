"""Tests for per-session state machine aggregation."""

from tools.lightd.state_machine import (
    STATE_BUSY,
    STATE_DONE,
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
    assert sm.state.resolve() == STATE_BUSY


def test_session_stop_sets_done_when_all_idle():
    sm = StateMachine(done_timeout_sec=60)
    sm.apply("user_prompt", session_id="a")
    sm.apply("session_stop", session_id="a")
    assert sm.state.resolve() == STATE_DONE


def test_tool_start_clears_waiting_for_session():
    sm = StateMachine()
    sm.apply("permission_wait", session_id="a")
    assert sm.state.resolve() == STATE_WAITING
    sm.apply("tool_start", session_id="a")
    assert sm.state.waiting_count == 0
    assert sm.state.busy_count == 1


def test_trae_permission_done_via_post_tool_path():
    sm = StateMachine()
    sm.apply("permission_wait", session_id="a")
    sm.apply("permission_done", session_id="a")
    assert sm.state.waiting_count == 0
