import json
import logging
import queue
from unittest.mock import Mock

from client.agent_logging import (
    JsonEventFormatter,
    LoggingRuntime,
    ResilientQueueHandler,
    ResilientRotatingFileHandler,
    start_agent_logging,
)


class Config:
    log_path = ""
    log_max_bytes = 1024
    log_backup_count = 2


def test_rollover_permission_error_does_not_escape(tmp_path, monkeypatch):
    handler = ResilientRotatingFileHandler(
        tmp_path / "agent.log", maxBytes=1, backupCount=2
    )
    monkeypatch.setattr(
        handler, "doRollover", Mock(side_effect=PermissionError(32, "locked"))
    )
    handler.emit(logging.makeLogRecord({"msg": "event"}))
    handler.emit(logging.makeLogRecord({"msg": "event-2"}))
    assert handler.rollover_failures == 1


def test_event_formatter_writes_required_json_fields():
    record = logging.makeLogRecord(
        {
            "msg": "PROCESS_START",
            "event": "PROCESS_START",
            "state": "STARTING",
            "attempt": 0,
        }
    )
    payload = json.loads(JsonEventFormatter().format(record))
    assert set(payload) == {"timestamp", "event", "state", "attempt"}
    assert payload["event"] == "PROCESS_START"


def test_queue_handler_reports_drops_after_recovery():
    events = queue.Queue(maxsize=1)
    handler = ResilientQueueHandler(events)
    handler.enqueue(logging.makeLogRecord({"msg": "first"}))
    handler.enqueue(logging.makeLogRecord({"msg": "lost"}))
    assert handler.dropped_events == 1
    events.get_nowait()
    handler.enqueue(logging.makeLogRecord({"msg": "after"}))
    recovered = events.get_nowait()
    assert recovered.event == "LOG_EVENTS_DROPPED"
    assert recovered.dropped == 1


def test_stop_is_bounded_when_listener_cannot_flush(tmp_path, monkeypatch):
    config = Config()
    config.log_path = str(tmp_path / "agent.log")
    runtime = start_agent_logging(config)
    try:
        listener_thread = runtime.listener._thread
        monkeypatch.setattr(listener_thread, "join", Mock())
        assert runtime.stop(0) is False
    finally:
        if runtime.listener._thread and runtime.listener._thread.is_alive():
            runtime.listener._thread.join(1)


def test_stop_full_queue_preserves_accepted_event_and_reports_timeout(caplog):
    events = queue.Queue(maxsize=1)
    accepted = logging.makeLogRecord({"msg": "accepted"})
    events.put_nowait(accepted)
    logger = logging.getLogger("managed-agent-stop-test")
    handler = ResilientQueueHandler(events)
    listener = logging.handlers.QueueListener(events, logging.NullHandler())
    runtime = LoggingRuntime(logger, handler, listener)

    assert runtime.stop(0) is False
    assert events.get_nowait() is accepted
    assert "LOG_FLUSH_TIMEOUT" in caplog.text
