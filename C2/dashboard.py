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
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from C2.managed_dashboard import ManagedDashboardData

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
    managed_data: ManagedDashboardData | None = None,
):
    """Factory so the Textual import stays lazy and testable."""
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.css.query import NoMatches
    from textual.widgets import DataTable, Footer, Header, Input, Static, TabbedContent, TabPane
    from rich.text import Text

    class C2DashboardApp(App):
        """Live client dashboard.

        Runs entirely on the Textual asyncio loop; ``_refresh`` is driven by
        ``set_interval`` and only reads the in-memory snapshot, so it can
        never block the C2 accept/command sockets.
        """

        BINDINGS = [
            ("ctrl+r", "refresh", "Refresh"),
            ("m", "managed", "Managed Agents"),
            ("d", "disconnect", "Disconnect"),
            ("r", "revoke", "Revoke"),
            ("f", "filter", "Filter"),
            ("q", "quit", "Quit"),
            ("y", "confirm_yes", "Yes"),
            ("n", "confirm_no", "No"),
        ]

        CSS = """
        #managed-banner { color: red; text-style: bold; }
        #managed-message { min-height: 1; }
        #managed-detail { min-height: 5; }
        #managed-devices { height: 12; }
        #managed-audit { height: 10; }
        .hidden { display: none; }
        """

        def __init__(self, dashboard_data: DashboardData, interval: float = 2.0, **kw):
            super().__init__(**kw)
            self._data = dashboard_data
            self._managed_data = managed_data
            self._interval = interval
            self._started = time.time()
            self._managed_snapshot = managed_data.snapshot() if managed_data is not None else None
            self._confirming_disconnect = False
            self._closing = False

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with TabbedContent(id="dashboard-tabs"):
                with TabPane("Legacy", id="legacy"):
                    with Vertical():
                        yield Static("", id="status", classes="status")
                        yield DataTable(id="clients")
                with TabPane("Managed Agents", id="managed"):
                    with Vertical():
                        yield Static("", id="managed-banner")
                        yield Input(placeholder="Filter by device, agent ID, or status", id="managed-filter")
                        yield DataTable(id="managed-devices")
                        yield Static("", id="managed-detail")
                        yield DataTable(id="managed-audit")
                        yield Static("", id="managed-confirm", classes="hidden")
                        with Vertical(id="revoke-form", classes="hidden"):
                            yield Input(placeholder="First 8 characters of agent ID", id="revoke-id", max_length=8)
                            yield Input(placeholder="Revocation reason", id="revoke-reason", max_length=512)
                        yield Static("", id="managed-message")
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#clients", DataTable)
            table.add_columns("ID", "User", "IP", "Port", "Latency", "Quality", "Cmds", "State")
            managed_table = self.query_one("#managed-devices", DataTable)
            managed_table.cursor_type = "row"
            managed_table.add_columns("Status", "Device", "Agent ID", "VPN IP", "Last Seen", "Certificate Expiry")
            audit = self.query_one("#managed-audit", DataTable)
            audit.add_columns("Timestamp", "Action", "Result", "Actor", "Target", "Reason")
            self.set_interval(self._interval, self._refresh)
            self.set_interval(self._interval, self._start_managed_refresh)
            self._refresh()
            if self._managed_snapshot is not None:
                self._apply_managed_snapshot(self._managed_snapshot)
                self._start_managed_refresh(force=True)

        def on_unmount(self) -> None:
            self._closing = True
            self.workers.cancel_all()

        def _refresh(self) -> None:
            try:
                table = self.query_one("#clients", DataTable)
                status = self.query_one("#status", Static)
            except NoMatches:
                return
            try:
                rows = self._data.refresh_if_stale()
                summary = self._data.summary()
            except Exception as exc:  # defensive: never let the loop die
                status.update(f"[red]snapshot error: {exc}[/red]")
                return
            table.clear()
            for row in rows:
                state = "ON" if row.connected else "off"
                table.add_row(
                    row.client_id[:8], row.username, row.ip, str(row.port),
                    f"{row.latency:.0f}ms" if row.latency > 0 else "-",
                    row.quality, str(row.total_commands), state,
                )
            uptime = int(time.time() - self._started)
            status.update(
                f"[bold cyan]Uptime {uptime}s[/bold cyan] | "
                f"Total [bold]{int(summary['total'])}[/bold] | "
                f"Connected [bold green]{int(summary['connected'])}[/bold green] | "
                f"Avg latency [bold]{summary['avg_latency_ms']}ms[/bold] | "
                f"Commands [bold]{int(summary['total_commands'])}[/bold]"
            )

        def action_refresh(self) -> None:
            self._refresh()
            self._start_managed_refresh(force=True)

        def action_managed(self) -> None:
            self.query_one("#dashboard-tabs", TabbedContent).active = "managed"
            if self._managed_data is None:
                self._message("Managed registry is not configured.")

        def action_filter(self) -> None:
            if self.query_one("#dashboard-tabs", TabbedContent).active == "managed":
                self.query_one("#managed-filter", Input).focus()

        def action_disconnect(self) -> None:
            if not self._managed_actions_available():
                return
            self._confirming_disconnect = True
            prompt = self.query_one("#managed-confirm", Static)
            prompt.remove_class("hidden")
            prompt.update(Text("Disconnect selected device? Press Y or N."))

        def action_confirm_no(self) -> None:
            self._confirming_disconnect = False
            self.query_one("#managed-confirm", Static).add_class("hidden")

        def action_confirm_yes(self) -> None:
            if not self._confirming_disconnect:
                return
            self.action_confirm_no()
            selected = self._managed_snapshot.selected
            self._start_managed_action("disconnect", selected.agent_id, "dashboard disconnect")

        def action_revoke(self) -> None:
            if self.query_one("#dashboard-tabs", TabbedContent).active != "managed":
                self.action_refresh()
                return
            if not self._managed_actions_available():
                return
            form = self.query_one("#revoke-form", Vertical)
            form.remove_class("hidden")
            agent_input = self.query_one("#revoke-id", Input)
            self.query_one("#revoke-reason", Input).value = ""
            agent_input.value = ""
            agent_input.focus()

        def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id == "managed-filter" and self._managed_snapshot is not None:
                self._render_managed_devices()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            if event.input.id != "revoke-reason":
                return
            short_id = self.query_one("#revoke-id", Input).value.strip()
            reason = event.value.strip()
            selected = self._managed_snapshot.selected
            if short_id.casefold() != selected.agent_id[:8].casefold():
                self._close_revoke_form()
                self._message("agent ID does not match selected device")
                return
            if not reason:
                self._close_revoke_form()
                self._message("revocation reason is required")
                return
            self._close_revoke_form()
            self._start_managed_action("revoke", selected.agent_id, reason)

        def _close_revoke_form(self) -> None:
            self.query_one("#revoke-form", Vertical).add_class("hidden")
            self.query_one("#managed-devices", DataTable).focus()

        def _managed_actions_available(self) -> bool:
            if self.query_one("#dashboard-tabs", TabbedContent).active != "managed":
                return False
            if self._managed_snapshot is None or self._managed_snapshot.selected is None:
                self._message("No managed device selected.")
                return False
            if not self._managed_snapshot.registry_available:
                self._message("Managed actions are disabled while registry is unavailable.")
                return False
            return True

        def _start_managed_refresh(self, force: bool = False) -> None:
            if self._managed_data is None or self._closing:
                return

            def task():
                snapshot = self._managed_data.refresh() if force else self._managed_data.refresh_if_stale()
                if not self._closing:
                    try:
                        self.call_from_thread(self._apply_managed_snapshot, snapshot)
                    except RuntimeError:
                        pass

            self.run_worker(task, group="managed-refresh", exclusive=True, thread=True, exit_on_error=False)

        def _start_managed_action(self, action: str, agent_id: str, reason: str) -> None:
            def task():
                operation = getattr(self._managed_data, action)
                result = operation(agent_id, "operator", reason)
                snapshot = self._managed_data.refresh()
                if not self._closing:
                    try:
                        self.call_from_thread(self._apply_action_result, result, snapshot)
                    except RuntimeError:
                        pass

            self.run_worker(task, group="managed-action", exclusive=True, thread=True, exit_on_error=False)

        def _apply_action_result(self, result, snapshot) -> None:
            self._message(f"{result.code}: {result.message}")
            self._apply_managed_snapshot(snapshot)

        def _apply_managed_snapshot(self, snapshot) -> None:
            if self._closing:
                return
            try:
                self.query_one("#managed-banner", Static).update(
                    Text("" if snapshot.registry_available else "REGISTRY UNAVAILABLE - showing last-good snapshot", style="red bold")
                )
            except NoMatches:
                return
            self._managed_snapshot = snapshot
            self._render_managed_devices()
            self._render_managed_detail()
            self._render_managed_audit()

        def _render_managed_devices(self) -> None:
            table = self.query_one("#managed-devices", DataTable)
            table.clear()
            query = self.query_one("#managed-filter", Input).value.strip().casefold()
            colors = {"ONLINE": "green", "OFFLINE": "yellow", "REVOKED": "red"}
            for row in self._managed_snapshot.devices:
                searchable = f"{row.display_name} {row.agent_id} {row.state}".casefold()
                if query and query not in searchable:
                    continue
                table.add_row(
                    Text(row.state, style=colors.get(row.state, "white")),
                    row.display_name,
                    row.agent_id,
                    row.last_vpn_ip or "-",
                    row.last_seen_at or "-",
                    row.certificate_not_after,
                    key=row.agent_id,
                )

        def _render_managed_detail(self) -> None:
            detail = self._managed_snapshot.selected
            if detail is None:
                text = "No managed device selected."
            else:
                revocation = detail.revoked_at or "not revoked"
                if detail.revocation_reason:
                    revocation += f" ({detail.revocation_reason})"
                text = (
                    f"Version: {detail.agent_version}\nFingerprint: {detail.certificate_fingerprint}\n"
                    f"Enrollment: {detail.enrolled_at}\nLast heartbeat: {detail.last_seen_at or '-'}\n"
                    f"Revocation: {revocation}"
                )
            self.query_one("#managed-detail", Static).update(Text(text))

        def _render_managed_audit(self) -> None:
            table = self.query_one("#managed-audit", DataTable)
            table.clear()
            for event in self._managed_snapshot.audit_events:
                table.add_row(
                    event.occurred_at,
                    event.action,
                    event.result,
                    event.actor,
                    event.target_agent_id or "-",
                    event.reason or "-",
                )

        def _message(self, message: str) -> None:
            try:
                self.query_one("#managed-message", Static).update(Text(message))
            except NoMatches:
                pass

    app = C2DashboardApp(data, refresh_interval)
    app.title = title
    return app


# --- Thread entry point (called from C2.main) --------------------------------

def start_dashboard(
    client_manager: object,
    port: int = 7000,
    refresh_interval: float = 2.0,
    title: str = "PhantomLink C2 - Live Dashboard",
) -> None:
    """Run the Textual dashboard. Intended to be started in a daemon thread
    from ``C2.main()`` (which already does so on port 7000).

    Contract:
    * Never binds a socket and never touches the C2 command channel — the
      TUI only polls the in-memory client snapshot, so the async UI loop
      cannot block the TCP accept/command path.
    * Degrades silently when there is no interactive terminal (tests, CI,
      background runs) or Textual is unavailable.
    * ``port`` is accepted for signature compatibility with the legacy
      ``start_dashboard(client_manager, 7000)`` call site; it is reserved for
      a future web view and is intentionally not bound here.
    """
    import sys

    if not sys.stdin.isatty():
        return  # headless / test environment: nothing to draw

    data = DashboardData(client_manager, refresh_interval=refresh_interval)
    try:
        app = build_app(data, title=title, refresh_interval=refresh_interval)
        app.run()
    except Exception:
        # TUI is best-effort; a dashboard crash must never kill the C2 shell.
        return
