"""Tests for event log."""

from tools.lightd.event_log import EventLog


def test_event_log_order_and_limit():
    log = EventLog(maxlen=3)
    for i in range(5):
        log.add(f"e{i}", "idle")
    rows = log.list(limit=10)
    assert len(rows) == 3
    assert rows[0]["event"] == "e4"
