from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

from C2.managed_registry import (
    ActionResult,
    AuditEvent,
    DeviceDetail,
    DeviceSummary,
    RegistryUnavailable,
    utc_now,
)
from C2.managed_services import DeviceActionService, DeviceQueryService


@dataclass(frozen=True)
class ManagedDashboardSnapshot:
    devices: tuple[DeviceSummary, ...]
    audit_events: tuple[AuditEvent, ...]
    selected: DeviceDetail | None
    registry_available: bool
    captured_at: str
    error_code: str | None


class ManagedDashboardData:
    """Small, thread-safe cache between managed services and the TUI."""

    def __init__(
        self,
        query_service: DeviceQueryService,
        action_service: DeviceActionService,
        *,
        refresh_interval: float = 2.0,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._query = query_service
        self._actions = action_service
        self._refresh_interval = float(refresh_interval)
        self._now = now
        self._lock = threading.Lock()
        self._hints: queue.Queue[None] = queue.Queue(maxsize=256)
        self._last_refresh: datetime | None = None
        self._details: dict[str, DeviceDetail] = {}
        self._selected_agent_id: str | None = None
        self._snapshot = ManagedDashboardSnapshot((), (), None, True, "", None)

    def refresh(self) -> ManagedDashboardSnapshot:
        """Read the repository without the cache lock, then atomically swap."""
        current_time = self._read_now()
        captured = current_time.isoformat(timespec="microseconds").replace("+00:00", "Z")
        try:
            devices = tuple(self._query.list_devices())
            details = {
                device.agent_id: detail
                for device in devices
                if (detail := self._query.get_device(device.agent_id)) is not None
            }
            events = tuple(self._query.list_audit_events(100))
        except RegistryUnavailable:
            with self._lock:
                previous = self._snapshot
                fresh = ManagedDashboardSnapshot(
                    previous.devices,
                    previous.audit_events,
                    previous.selected,
                    False,
                    previous.captured_at,
                    "REGISTRY_UNAVAILABLE",
                )
                self._snapshot = fresh
                self._last_refresh = current_time
        else:
            with self._lock:
                selected_id = self._selected_agent_id
                if selected_id not in details:
                    selected_id = next(
                        (device.agent_id for device in devices if device.agent_id in details),
                        None,
                    )
                selected = details.get(selected_id) if selected_id is not None else None
                fresh = ManagedDashboardSnapshot(devices, events, selected, True, captured, None)
                self._details = details
                self._selected_agent_id = selected_id
                self._snapshot = fresh
                self._last_refresh = current_time
        self._discard_hints()
        return fresh

    def refresh_if_stale(self) -> ManagedDashboardSnapshot:
        now = self._read_now()
        with self._lock:
            stale = self._last_refresh is None or (now - self._last_refresh).total_seconds() >= self._refresh_interval
        hinted = not self._hints.empty()
        return self.refresh() if stale or hinted else self.snapshot()

    def snapshot(self) -> ManagedDashboardSnapshot:
        with self._lock:
            return self._snapshot

    def select(self, agent_id: str | None) -> ManagedDashboardSnapshot:
        """Select from the last immutable detail cache; never touches storage."""
        with self._lock:
            selected = self._details.get(agent_id) if agent_id is not None else None
            self._selected_agent_id = selected.agent_id if selected is not None else None
            self._snapshot = replace(self._snapshot, selected=selected)
            return self._snapshot

    def notify_event(self) -> None:
        try:
            self._hints.put_nowait(None)
        except queue.Full:
            pass

    def disconnect(self, agent_id: str, actor: str, reason: str) -> ActionResult:
        if not self.snapshot().registry_available:
            return ActionResult("FAILED", "Managed actions disabled while registry is unavailable.", "")
        return self._actions.disconnect(agent_id, actor, reason)

    def revoke(self, agent_id: str, actor: str, reason: str) -> ActionResult:
        if not self.snapshot().registry_available:
            return ActionResult("FAILED", "Managed actions disabled while registry is unavailable.", "")
        return self._actions.revoke(agent_id, actor, reason)

    def _read_now(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("time source must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _discard_hints(self) -> None:
        while True:
            try:
                self._hints.get_nowait()
            except queue.Empty:
                return
