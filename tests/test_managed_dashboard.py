import asyncio
import threading
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from C2.dashboard import DashboardData, build_app
from C2.managed_dashboard import ManagedDashboardData
from C2.managed_registry import ActionResult, AuditEvent, DeviceDetail, DeviceSummary, RegistryUnavailable


NOW = datetime(2026, 8, 13, 6, 0, tzinfo=timezone.utc)
AGENT_ID = "11111111-1111-4111-8111-111111111111"
BETA_ID = "22222222-2222-4222-8222-222222222222"


class Query:
    def __init__(self):
        self.raise_registry_unavailable = False
        self.list_calls = 0
        self.get_calls = 0
        self.devices = (
            DeviceSummary(AGENT_ID, "pc-01", "ONLINE", "10.8.0.21", "2026-08-13T05:59:00Z", "2027-08-13T00:00:00Z", "2.0"),
        )
        self.detail = DeviceDetail(
            AGENT_ID, "pc-01", "ONLINE", "10.8.0.21", "2026-08-13T05:59:00Z",
            "2027-08-13T00:00:00Z", "2.0", "aa:bb", "123", "2026-08-01T00:00:00Z", None, None,
        )
        self.details = {AGENT_ID: self.detail}
        self.events = (
            AuditEvent(1, "2026-08-13T05:58:00Z", "operator", "ENROLLED", AGENT_ID, "SUCCEEDED", "approved", "corr", ()),
        )

    def _check(self):
        if self.raise_registry_unavailable:
            raise RegistryUnavailable("registry unavailable")

    def list_devices(self):
        self.list_calls += 1
        self._check()
        return self.devices

    def get_device(self, agent_id):
        self.get_calls += 1
        self._check()
        return self.details.get(agent_id)

    def list_audit_events(self, limit=100):
        self._check()
        assert limit == 100
        return self.events


class Actions:
    def __init__(self):
        self.disconnect_calls = []
        self.revoke_calls = []

    def disconnect(self, agent_id, actor, reason):
        self.disconnect_calls.append((agent_id, actor, reason))
        return ActionResult("DISCONNECTED", "Device disconnected.", "corr-d")

    def revoke(self, agent_id, actor, reason):
        self.revoke_calls.append((agent_id, actor, reason))
        return ActionResult("REVOKED", "Device revoked.", "corr-r")


@pytest.fixture
def query():
    return Query()


@pytest.fixture
def actions():
    return Actions()


@pytest.fixture
def managed(query, actions):
    data = ManagedDashboardData(query, actions, now=lambda: NOW)
    data.refresh()
    return data


@pytest.fixture
def app(managed):
    legacy = DashboardData(snapshot_fn=lambda: {
        "legacy-id": {"username": "alice", "addr": ("10.0.0.1", 5000), "active": True},
    })
    return build_app(legacy, refresh_interval=3600, managed_data=managed)


def add_beta(query):
    alpha = DeviceSummary(AGENT_ID, "alpha", "ONLINE", "10.8.0.21", "2026-08-13T05:59:00Z", "2027-08-13T00:00:00Z", "2.0")
    beta = DeviceSummary(BETA_ID, "beta", "OFFLINE", "10.8.0.22", "2026-08-13T05:57:00Z", "2027-09-13T00:00:00Z", "3.0")
    query.devices = (alpha, beta)
    query.details[AGENT_ID] = DeviceDetail(
        AGENT_ID, "alpha", "ONLINE", "10.8.0.21", "2026-08-13T05:59:00Z",
        "2027-08-13T00:00:00Z", "2.0", "alpha-fingerprint", "123", "2026-08-01T00:00:00Z", None, None,
    )
    query.details[BETA_ID] = DeviceDetail(
        BETA_ID, "beta", "OFFLINE", "10.8.0.22", "2026-08-13T05:57:00Z",
        "2027-09-13T00:00:00Z", "3.0", "beta-fingerprint", "456", "2026-08-02T00:00:00Z", None, None,
    )


@pytest.fixture
def two_device_app():
    query, actions = Query(), Actions()
    add_beta(query)
    managed = ManagedDashboardData(query, actions, now=lambda: NOW)
    managed.refresh()
    legacy = DashboardData(snapshot_fn=lambda: {})
    return build_app(legacy, refresh_interval=3600, managed_data=managed), query, actions, managed


def test_registry_failure_keeps_labeled_last_snapshot_and_disables_actions(query, actions):
    data = ManagedDashboardData(query, actions, now=lambda: NOW)
    healthy = data.refresh()
    query.raise_registry_unavailable = True
    degraded = data.refresh()
    assert degraded.devices == healthy.devices
    assert degraded.audit_events == healthy.audit_events
    assert degraded.selected == healthy.selected
    assert degraded.registry_available is False
    assert degraded.error_code == "REGISTRY_UNAVAILABLE"
    assert degraded.captured_at == healthy.captured_at
    result = data.revoke(healthy.devices[0].agent_id, "operator", "retired")
    assert result.code == "FAILED"
    assert actions.revoke_calls == []


def test_empty_degraded_snapshot_is_labeled_and_frozen(query, actions):
    query.raise_registry_unavailable = True
    snapshot = ManagedDashboardData(query, actions, now=lambda: NOW).refresh()
    assert snapshot.devices == () and snapshot.audit_events == () and snapshot.selected is None
    assert snapshot.registry_available is False
    with pytest.raises(FrozenInstanceError):
        snapshot.registry_available = True


def test_refresh_if_stale_and_bounded_hints(query, actions):
    times = iter((NOW, NOW, NOW.replace(second=1), NOW.replace(second=3)))
    data = ManagedDashboardData(query, actions, now=lambda: next(times))
    data.refresh()
    data.refresh_if_stale()
    assert query.list_calls == 1
    for _ in range(300):
        data.notify_event()
    data.refresh_if_stale()
    assert query.list_calls == 2


def test_repository_io_does_not_hold_snapshot_lock(query, actions):
    entered = threading.Event()
    release = threading.Event()
    original = query.list_devices

    def blocked():
        entered.set()
        release.wait(2)
        return original()

    query.list_devices = blocked
    data = ManagedDashboardData(query, actions, now=lambda: NOW)
    thread = threading.Thread(target=data.refresh)
    thread.start()
    assert entered.wait(1)
    assert data.snapshot().devices == ()
    release.set()
    thread.join(2)
    assert not thread.is_alive()


def test_managed_tab_has_required_columns_and_text_status(app):
    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("m")
            await pilot.pause()
            from textual.widgets import DataTable
            table = app.query_one("#managed-devices", DataTable)
            assert [str(column.label) for column in table.columns.values()] == [
                "Status", "Device", "Agent ID", "VPN IP", "Last Seen", "Certificate Expiry",
            ]
            assert "ONLINE" in [str(cell) for cell in table.get_row_at(0)]
    asyncio.run(scenario())


def test_tabs_detail_audit_and_legacy_are_present(app):
    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            from textual.widgets import DataTable, Static
            assert len(app.query_one("#clients", DataTable).rows) == 1
            await pilot.press("m")
            await pilot.pause()
            detail = str(app.query_one("#managed-detail", Static).render())
            audit = app.query_one("#managed-audit", DataTable)
            assert all(label in detail for label in ("Version", "Fingerprint", "Enrollment", "Last heartbeat", "Revocation"))
            assert [str(column.label) for column in audit.columns.values()] == [
                "Timestamp", "Action", "Result", "Actor", "Target", "Reason",
            ]
            assert len(audit.rows) == 1
    asyncio.run(scenario())


def test_filter_matches_name_id_and_status(app):
    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("m", "f")
            await pilot.press(*"offline")
            await pilot.pause()
            from textual.widgets import DataTable, Input
            assert app.query_one("#managed-filter", Input).has_focus
            assert len(app.query_one("#managed-devices", DataTable).rows) == 0
            await pilot.press(*(["backspace"] * len("offline")), *AGENT_ID[:8])
            await pilot.pause()
            assert len(app.query_one("#managed-devices", DataTable).rows) == 1
    asyncio.run(scenario())


def test_disconnect_requires_confirmation(app, actions):
    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("m", "d")
            await pilot.pause()
            from textual.widgets import Static
            assert "Disconnect" in str(app.query_one("#managed-confirm", Static).render())
            await pilot.press("n", "d", "y")
            await pilot.pause(0.1)
            assert actions.disconnect_calls == [(AGENT_ID, "operator", "dashboard disconnect")]
    asyncio.run(scenario())


def test_revoke_requires_short_id_and_reason(app, actions):
    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("m", "r")
            await pilot.press(*"wrong-id", "tab", *"retired", "enter")
            await pilot.pause()
            from textual.widgets import Static
            assert actions.revoke_calls == []
            assert "agent ID does not match" in str(app.query_one("#managed-message", Static).render())
            await pilot.press("r", *AGENT_ID[:8], "tab", *"retired", "enter")
            await pilot.pause(0.1)
            assert actions.revoke_calls == [(AGENT_ID, "operator", "retired")]
    asyncio.run(scenario())


def test_degraded_banner_disables_managed_mutations(app, managed, query, actions):
    query.raise_registry_unavailable = True
    managed.refresh()

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("m", "d", "y", "r")
            await pilot.pause()
            from textual.widgets import Static
            assert "REGISTRY UNAVAILABLE" in str(app.query_one("#managed-banner", Static).render())
            assert "disabled" in str(app.query_one("#managed-message", Static).render()).lower()
            assert actions.disconnect_calls == [] and actions.revoke_calls == []
    asyncio.run(scenario())


def test_q_quits_and_arrow_tab_navigation_is_safe(app):
    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("m", "down", "up", "tab", "shift+tab", "q")
            await pilot.pause()
    asyncio.run(scenario())


def test_second_row_updates_detail_without_repository_io(two_device_app):
    app, query, _actions, managed = two_device_app

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("m")
            await pilot.pause(0.1)
            from textual.widgets import DataTable, Static
            table = app.query_one("#managed-devices", DataTable)
            calls_before = (query.list_calls, query.get_calls)
            table.focus()
            await pilot.press("down")
            await pilot.pause()
            assert table.cursor_row == 1
            detail = str(app.query_one("#managed-detail", Static).render())
            assert "beta-fingerprint" in detail and "Version: 3.0" in detail
            assert managed.snapshot().selected.agent_id == BETA_ID
            assert (query.list_calls, query.get_calls) == calls_before
    asyncio.run(scenario())


def test_second_row_disconnect_targets_beta_only(two_device_app):
    app, _query, actions, _managed = two_device_app

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("m")
            from textual.widgets import DataTable
            app.query_one("#managed-devices", DataTable).focus()
            await pilot.press("down", "d", "y")
            await pilot.pause(0.1)
            assert actions.disconnect_calls == [(BETA_ID, "operator", "dashboard disconnect")]
    asyncio.run(scenario())


def test_second_row_revoke_accepts_beta_short_id_and_targets_beta_only(two_device_app):
    app, _query, actions, _managed = two_device_app

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("m")
            from textual.widgets import DataTable
            app.query_one("#managed-devices", DataTable).focus()
            await pilot.press("down", "r", *BETA_ID[:8], "tab", *"retired", "enter")
            await pilot.pause(0.1)
            assert actions.revoke_calls == [(BETA_ID, "operator", "retired")]
    asyncio.run(scenario())


def test_refresh_preserves_selected_agent_id_until_device_disappears(query, actions):
    add_beta(query)
    data = ManagedDashboardData(query, actions, now=lambda: NOW)
    data.refresh()
    data.select(BETA_ID)
    query.devices = tuple(reversed(query.devices))
    assert data.refresh().selected.agent_id == BETA_ID
    query.devices = (next(device for device in query.devices if device.agent_id == AGENT_ID),)
    assert data.refresh().selected.agent_id == AGENT_ID
    query.devices = ()
    assert data.refresh().selected is None


def test_filter_preserves_visible_selection_then_chooses_first_or_none(two_device_app):
    app, _query, _actions, managed = two_device_app

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("m")
            from textual.widgets import DataTable
            table = app.query_one("#managed-devices", DataTable)
            table.focus()
            await pilot.press("down", "f", *"beta")
            await pilot.pause()
            assert managed.snapshot().selected.agent_id == BETA_ID
            await pilot.press(*(["backspace"] * 4), *"alpha")
            await pilot.pause()
            assert managed.snapshot().selected.agent_id == AGENT_ID
            await pilot.press(*(["backspace"] * 5), *"missing")
            await pilot.pause()
            assert managed.snapshot().selected is None and len(table.rows) == 0
            await pilot.press(*(["backspace"] * 7))
            await pilot.pause()
            assert managed.snapshot().selected.agent_id == AGENT_ID
            assert table.cursor_row == 0
    asyncio.run(scenario())


def test_degraded_snapshot_retains_beta_selection_and_disables_actions(query, actions):
    add_beta(query)
    data = ManagedDashboardData(query, actions, now=lambda: NOW)
    data.refresh()
    data.select(BETA_ID)
    query.raise_registry_unavailable = True
    degraded = data.refresh()
    assert degraded.selected.agent_id == BETA_ID
    assert degraded.registry_available is False
    assert data.disconnect(BETA_ID, "operator", "maintenance").code == "FAILED"
    assert data.revoke(BETA_ID, "operator", "retired").code == "FAILED"
    assert actions.disconnect_calls == [] and actions.revoke_calls == []
