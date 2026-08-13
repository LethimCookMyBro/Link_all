"""Tests for the C2 dashboard (C2/dashboard.py).

The data layer is pure and synchronous — tested directly. The Textual app is
exercised headlessly through App.run_test() inside asyncio.run, so no real
terminal, no sockets, and no hanging.
"""
import threading
import time

import pytest

from C2.dashboard import DashboardData, build_app


def make_health(latency=0.0, quality="good", total=0):
    class H:
        def get_stats(self):
            return {"latency": latency, "quality": quality, "total_commands": total}
    return H()


def sample_snapshot():
    return {
        "aaa": {"username": "alice", "addr": ("10.0.0.1", 5000),
                "health": make_health(120.0, "good", 3), "active": True},
        "bbb": {"username": "bob", "addr": ("10.0.0.2", 5001),
                "health": make_health(80.0, "excellent", 7), "active": True},
        "ccc": {"username": "carol", "addr": ("10.0.0.3", 5002),
                "health": make_health(0.0, "n/a", 0), "active": False},
    }


class TestDashboardData:
    def test_requires_a_source(self):
        with pytest.raises(ValueError):
            DashboardData()

    def test_refresh_builds_rows(self):
        d = DashboardData(snapshot_fn=sample_snapshot)
        rows = d.refresh()
        assert len(rows) == 3
        by_id = {r.client_id: r for r in rows}
        assert by_id["aaa"].username == "alice"
        assert by_id["aaa"].ip == "10.0.0.1"
        assert by_id["aaa"].port == 5000
        assert by_id["aaa"].latency == 120.0
        assert by_id["ccc"].connected is False

    def test_supports_client_manager(self):
        class CM:
            def list_clients(self):
                return sample_snapshot()
        d = DashboardData(client_manager=CM())
        assert len(d.refresh()) == 3

    def test_missing_fields_are_tolerated(self):
        d = DashboardData(snapshot_fn=lambda: {
            "zzz": {"username": "minimal"},
        })
        rows = d.refresh()
        assert rows[0].ip == "?"
        assert rows[0].port == 0
        assert rows[0].latency == 0.0
        assert rows[0].quality == "n/a"
        assert rows[0].connected is True  # default

    def test_failing_snapshot_never_raises(self):
        def boom():
            raise RuntimeError("snapshot broke")
        d = DashboardData(snapshot_fn=boom)
        assert d.refresh() == []
        assert d.summary() == {
            "total": 0.0, "connected": 0.0, "avg_latency_ms": 0.0, "total_commands": 0.0,
        }

    def test_summary_stats(self):
        d = DashboardData(snapshot_fn=sample_snapshot)
        d.refresh()
        s = d.summary()
        assert s["total"] == 3.0
        assert s["connected"] == 2.0
        assert s["avg_latency_ms"] == 100.0  # (120+80)/2
        assert s["total_commands"] == 10.0

    def test_summary_no_clients_no_division_by_zero(self):
        d = DashboardData(snapshot_fn=lambda: {})
        d.refresh()
        assert d.summary()["avg_latency_ms"] == 0.0

    def test_refresh_if_stale_respects_interval(self):
        calls = []
        d = DashboardData(snapshot_fn=lambda: (calls.append(1), sample_snapshot())[1],
                          refresh_interval=3600.0)
        d.refresh()
        d.refresh_if_stale()  # not stale -> cache, no new call
        assert len(calls) == 1
        d2 = DashboardData(snapshot_fn=lambda: (calls.append(1), sample_snapshot())[1],
                           refresh_interval=0.0)
        d2.refresh()
        d2.refresh_if_stale()  # stale immediately -> refresh
        assert len(calls) == 3

    def test_snapshot_returns_a_copy(self):
        d = DashboardData(snapshot_fn=sample_snapshot)
        d.refresh()
        snapshot = d.snapshot()
        snapshot.clear()
        assert len(d.snapshot()) == 3

    def test_thread_safe_cache_access(self):
        d = DashboardData(snapshot_fn=sample_snapshot, refresh_interval=0.0)
        errors = []

        def writer():
            try:
                for _ in range(50):
                    d.refresh()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        def reader():
            try:
                for _ in range(50):
                    d.summary()
                    d.snapshot()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == []


class TestDashboardApp:
    def test_late_refresh_after_teardown_is_contained(self):
        import asyncio

        async def run():
            app = build_app(DashboardData(snapshot_fn=sample_snapshot), refresh_interval=0.05)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
            app._refresh()

        asyncio.run(run())

    def test_headless_app_builds_table(self):
        import asyncio

        async def run():
            app = build_app(DashboardData(snapshot_fn=sample_snapshot), refresh_interval=0.05)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                from textual.widgets import DataTable, Static
                table = app.query_one("#clients", DataTable)
                n_rows = len(table.rows)
                status = str(app.query_one("#status", Static).render())
                await pilot.pause()
                return n_rows, status

        n_rows, status = asyncio.run(run())
        assert n_rows == 3
        assert "Total" in status and "Connected" in status

    def test_app_survives_broken_snapshot(self):
        import asyncio

        async def run():
            def boom():
                raise RuntimeError("nope")
            app = build_app(DashboardData(snapshot_fn=boom), refresh_interval=0.05)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                from textual.widgets import DataTable
                table = app.query_one("#clients", DataTable)
                assert len(table.rows) == 0
                await pilot.pause()

        asyncio.run(run())  # must not raise/hang
