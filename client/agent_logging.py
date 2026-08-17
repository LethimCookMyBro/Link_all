from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from time import monotonic


class JsonEventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", record.getMessage())
        state = getattr(record, "state", "UNKNOWN")
        attempt = getattr(record, "attempt", 0)
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event if isinstance(event, str) else "INVALID_EVENT",
            "state": state if isinstance(state, str) else "UNKNOWN",
            "attempt": attempt
            if isinstance(attempt, int) and not isinstance(attempt, bool)
            else 0,
        }
        if hasattr(record, "dropped"):
            payload["dropped"] = record.dropped
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


class ResilientRotatingFileHandler(RotatingFileHandler):
    def __init__(self, filename, *args, clock=monotonic, **kwargs) -> None:
        super().__init__(filename, *args, **kwargs)
        self._clock = clock
        self.next_rollover_attempt = 0.0
        self.rollover_failures = 0

    def _reopen_append(self) -> None:
        if self.stream is not None:
            try:
                self.stream.close()
            except OSError:
                pass
        self.stream = self._open()

    def _rollover_failed(self) -> None:
        self.rollover_failures += 1
        self.next_rollover_attempt = self._clock() + 30.0
        try:
            self._reopen_append()
        except OSError:
            pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if (
                self.shouldRollover(record)
                and self._clock() >= self.next_rollover_attempt
            ):
                try:
                    self.doRollover()
                except PermissionError:
                    self._rollover_failed()
            logging.FileHandler.emit(self, record)
        except PermissionError:
            # A locked Windows log must never terminate the runtime owner.
            self._rollover_failed()


class ResilientQueueHandler(QueueHandler):
    def __init__(self, events: queue.Queue) -> None:
        super().__init__(events)
        self._dropped_events = 0
        self._dropped_lock = threading.Lock()

    @property
    def dropped_events(self) -> int:
        with self._dropped_lock:
            return self._dropped_events

    def _summary(self, dropped: int) -> logging.LogRecord:
        return logging.makeLogRecord(
            {
                "name": "managed_agent",
                "levelno": logging.WARNING,
                "levelname": "WARNING",
                "msg": "LOG_EVENTS_DROPPED",
                "event": "LOG_EVENTS_DROPPED",
                "state": "LOGGING",
                "attempt": 0,
                "dropped": dropped,
            }
        )

    def enqueue(self, record: logging.LogRecord) -> None:
        with self._dropped_lock:
            if self._dropped_events:
                try:
                    self.queue.put_nowait(self._summary(self._dropped_events))
                except queue.Full:
                    self._dropped_events += 1
                    return
                self._dropped_events = 0
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            with self._dropped_lock:
                self._dropped_events += 1


class LoggingRuntime:
    def __init__(
        self,
        logger: logging.Logger,
        handler: ResilientQueueHandler,
        listener: QueueListener,
    ) -> None:
        self.logger = logger
        self.handler = handler
        self.listener = listener
        self._stopped = False
        self._stop_lock = threading.Lock()
        self.flush_timed_out = False

    def emit(self, event: Mapping[str, object]) -> None:
        safe = {
            key: event[key]
            for key in ("event", "state", "attempt", "category", "delay")
            if key in event
        }
        name = safe.get("event", "LOG_EVENT")
        self.logger.info(name if isinstance(name, str) else "LOG_EVENT", extra=safe)

    def _flush_timeout(self) -> bool:
        self.flush_timed_out = True
        logging.getLogger(__name__).warning("LOG_FLUSH_TIMEOUT")
        return False

    def stop(self, timeout: float) -> bool:
        with self._stop_lock:
            if self._stopped and not self.flush_timed_out:
                return True
            self._stopped = True
            self.logger.removeHandler(self.handler)
            deadline = monotonic() + max(0.0, timeout)
            while True:
                try:
                    self.listener.enqueue_sentinel()
                    break
                except queue.Full:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        return self._flush_timeout()
                    threading.Event().wait(min(0.01, remaining))
            thread = self.listener._thread
            if thread is None:
                return self._flush_timeout()
            thread.join(max(0.0, deadline - monotonic()))
            if thread.is_alive():
                return self._flush_timeout()
            self.flush_timed_out = False
            self.handler.close()
            return True


def start_agent_logging(config) -> LoggingRuntime:
    logging.raiseExceptions = False
    events: queue.Queue = queue.Queue(maxsize=1000)
    queue_handler = ResilientQueueHandler(events)
    writer = ResilientRotatingFileHandler(
        Path(config.log_path),
        maxBytes=config.log_max_bytes,
        backupCount=config.log_backup_count,
        encoding="utf-8",
    )
    writer.setFormatter(JsonEventFormatter())
    listener = QueueListener(events, writer, respect_handler_level=True)
    logger = logging.getLogger("managed_agent")
    logger.setLevel(logging.INFO)
    logger.addHandler(queue_handler)
    listener.start()
    return LoggingRuntime(logger, queue_handler, listener)
