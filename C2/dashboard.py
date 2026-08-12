"""Textual reactive dashboard for the C2 console.

Design constraints (see the refactor plan):

* **Non-blocking.** The TUI runs its own asyncio event loop; it only polls an
  in-memory client snapshot (``client_manager.list_clients()`` or a supplied
  snapshot callback). It never reads from or writes to the C2 command socket,
  so the async UI loop can never stall the TCP accept/command path.
* **Testable.** ``DashboardData`` is pure and synchronous — no TTY, no asyncio,
  no sockets — and is unit-tested directly. The Textual ``C2DashboardApp`` is
  a thin view over it and is exercised through Textual's headless
  ``App.run_test()`` harness.
* **Graceful degradation.** If Textual is not installed, the module still
  imports and ``C2DashboardApp`` is unavailable; the data layer works anyway.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# --- Data layer (pure, synchronous, fully testable) -------------------------


@dataclass
class ClientRow:
    """Immutable snapshot of one client for the dashboard."""

    client_id: str
    username: str
    ip: str
    port: int
    latency: float
    quality: str
    total_commands: int
    connected: bool


class DashboardData:
    """Polls and caches client snapshots for the TUI.

    ``snapshot_fn`` (or a ClientManager with ``list_clients()``) must return
    a dict of ``client_id -> record`` with at least ``username``, ``addr``,
    ``health`` and ``active`` keys — exactly the shape ``ClientManager``
    produces. All access is guarded by a lock so the TUI thread and the C2
    worker threads never tear the cache.
    """

    def __init__(
        self,
        client_manager: Optional[object] = None,
        snapshot_fn: Optional[Callable[[], Dict]] = None,
        refresh_interval: float = 2.0,
    ) -> None:
        if snapshot_fn is None and client_manager is None:
            raise ValueError("provide client_manager or snapshot_fn")
        self._snapshot_fn = snapshot_fn or client_manager.list_clients
        self._lock = threading.Lock()
        self._cache: List[ClientRow] = []
        self._last_refresh = 0.0
        self._refresh_interval = refresh_interval

    # -- polling ------------------------------------------------------------
    def refresh(self) -> List[ClientRow]:
        """Pull a fresh snapshot and rebuild the row cache."""
        raw = {}
        try:
            raw = self._snapshot_fn() or {}
        except Exception:
            raw = {}  # a failing snapshot must never crash the TUI loop
        rows = []
        now = time.time()
        for cid, rec in raw.items():
            rows.append(self._to_row(str(cid), rec, now))
        with self._lock:
            self._cache = rows
            self._last_refresh = now
        return rows

    def refresh_if_stale(self) -> List[ClientRow]:
        """Refresh only if the cache is older than the interval."""
        with self._lock:
            stale = (time.time() - self._last_refresh) >= self._refresh_interval
        return self.refresh() if stale else self.snapshot()

    # -- read paths (never touch the source) --------------------------------
    def snapshot(self) -> List[ClientRow]:
        with self._lock:
            return list(self._cache)

    def summary(self) -> Dict[str, float]:
        """Aggregate stats: totals and average latency of connected clients."""
        rows = self.snapshot()
        connected = [r for r in rows if r.connected]
        latencies = [r.latency for r in connected if r.latency > 0]
        return {
            "total": float(len(rows)),
            "connected": float(len(connected)),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
            "total_commands": float(sum(r.total_commands for r in rows)),
        }

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _to_row(cid: str, rec: Dict, now: float) -> ClientRow:
        addr = rec.get("addr") or ("?", 0)
        ip = addr[0] if isinstance(addr, (tuple, list)) and addr else "?"
        port = addr[1] if isinstance(addr, (tuple, list)) and len(addr) > 1 else 0
        health = rec.get("health")
        latency = 0.0
        quality = "n/a"
        total = 0
        if health is not None and hasattr(health, "get_stats"):
            stats = health.get_stats()
            latency = float(stats.get("latency", 0) or 0)
            quality = str(stats.get("quality", "n/a"))
            total = int(stats.get("total_commands", 0) or 0)
        connected = bool(rec.get("active", True))
        return ClientRow(
            client_id=cid,
            username=str(rec.get("username", "?")),
            ip=str(ip),
            port=int(port),
            latency=latency,
            quality=quality,
            total_commands=total,
            connected=connected,
        )


# --- Textual TUI (thin view; textual required at runtime) --------------------

def build_app(
    data: DashboardData,
    title: str = "PhantomLink C2 - Live Dashboard",
    refresh_interval: float = 2.0,
):
    """Factory so the Textual import stays lazy and testable."""
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.widgets import DataTable, Footer, Header, Static

    class C2DashboardApp(App):
        """Live client dashboard.

        Runs entirely on the Textual asyncio loop; ``_refresh`` is driven by
        ``set_interval`` and only reads the in-memory snapshot, so it can
        never block the C2 accept/command sockets.
        """

        BINDINGS = [
            ("r", "refresh", "Refresh"),
            ("q", "quit", "Quit"),
        ]

        def __init__(self, dashboard_data: DashboardData, interval: float = 2.0, **kw):
            super().__init__(**kw)
            self._data = dashboard_data
            self._interval = interval
            self._started = time.time()

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical():
                yield Static("", id="status", classes="status")
                yield DataTable(id="clients")
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#clients", DataTable)
            table.add_columns("ID", "User", "IP", "Port", "Latency", "Quality", "Cmds", "State")
            self.set_interval(self._interval, self._refresh)
            self._refresh()

        def _refresh(self) -> None:
            try:
                rows = self._data.refresh_if_stale()
                summary = self._data.summary()
            except Exception as exc:  # defensive: never let the loop die
                self.query_one("#status", Static).update(f"[red]snapshot error: {exc}[/red]")
                return
            table = self.query_one("#clients", DataTable)
            table.clear()
            for row in rows:
                state = "ON" if row.connected else "off"
                table.add_row(
                    row.client_id[:8], row.username, row.ip, str(row.port),
                    f"{row.latency:.0f}ms" if row.latency > 0 else "-",
                    row.quality, str(row.total_commands), state,
                )
            uptime = int(time.time() - self._started)
            self.query_one("#status", Static).update(
                f"[bold cyan]Uptime {uptime}s[/bold cyan] | "
                f"Total [bold]{int(summary['total'])}[/bold] | "
                f"Connected [bold green]{int(summary['connected'])}[/bold green] | "
                f"Avg latency [bold]{summary['avg_latency_ms']}ms[/bold] | "
                f"Commands [bold]{int(summary['total_commands'])}[/bold]"
            )

        def action_refresh(self) -> None:
            self._refresh()

    app = C2DashboardApp(data, refresh_interval)
    app.title = title
    return app
