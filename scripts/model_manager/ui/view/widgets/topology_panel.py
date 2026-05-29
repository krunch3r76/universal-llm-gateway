"""Topology panel — live-probed node table that renders fleet-operation progress.

Nodes are displayed in a DataTable. Fleet operations (Sync + Restart All /
Rebuild + Deploy All) are orchestrated headlessly by ``controller.fleet``; this
widget supplies a progress sink that binds the orchestrator's line/status/focus
callbacks to its per-node log buffers and table cells, and clicking a row
switches the LogStream to that node's buffered output.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, DataTable, Select, Static

if TYPE_CHECKING:
    from ...app import ModelManagerApp

from scripts.model_manager.topology import TopologySnapshot, build_snapshot

from ...controller.fleet_remote import _hostname_from_stargate_id
from .log_stream import LogStream

logger = logging.getLogger(__name__)

_STATUS_COL = "status"
# Row key used for the master node.  All status updates target this key; if the
# master's stargate_id ever becomes user-configurable this constant must follow.
_MASTER_ROW_KEY = "localhost"
# Build scope options offered in the Select widget.  Keep in sync with the
# ./manage relay --scope CLI argument.
_BUILD_SCOPES: list[tuple[str, str]] = [
    ("vLLM + llama", "all"),
    ("llama only", "llama"),
]
_STATUS_REASON_DISPLAY: dict[str, str] = {
    "master_down": "master stargate is not running",
    "node_env_missing": "node env file missing under ~/.gateway/nodes",
    "no_recent_telemetry": "no recent federation telemetry for relay",
    "connected_no_models": "relay connected but zero models advertised",
    "connected_models_unknown": "relay connected but model source probe unavailable",
    "socket_dir_root_owned": "socket dir /tmp/universal-protocol root-owned (Docker bind-mount race — prevented by proactive ensure_socket_dir() in deploy scripts)",
}


class TopologyPanel(Widget):
    """DataTable showing all nodes with live status + parallel fleet operations."""

    class DeployStateChanged(Message):
        """Message posted when a fleet deploy operation changes state.

        Args:
            deploying: True while a deploy operation is running, False when complete.
        """

        def __init__(self, deploying: bool) -> None:
            self.deploying = deploying
            super().__init__()

    DEFAULT_CSS = """
    TopologyPanel {
        height: auto;
        max-height: 24;
    }
    #topo-table-container {
        height: auto;
        max-height: 12;
        padding: 0 2;
    }
    #topo-ops {
        height: auto;
        padding: 0 2;
    }
    #topo-ops Button {
        margin: 0 1;
    }
    #topo-build-scope {
        width: 28;
    }
    #topo-progress {
        display: none;
        height: auto;
        max-height: 8;
        padding: 0 2;
        border: none;
    }
    #topo-progress Log {
        height: auto;
        max-height: 7;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Buffered per-node deploy output used by row-selection log switching.
        self._workspace_root: Path | None = None
        self._node_buffers: dict[str, list[str]] = {}
        # Currently selected row/node for the progress log panel.
        self._active_node: str | None = None
        # True while fleet deployment workflow is active.
        self._deploying: bool = False
        # Incremented per deploy run so stale hide timers cannot collapse new logs.
        self._deploy_run_id: int = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="topo-table-container"):
            yield Static("[b]Topology[/b]", markup=True)
            yield DataTable(id="topo-table")
        with Horizontal(id="topo-ops"):
            yield Button("Sync + Restart All", id="btn-deploy-all", variant="warning")
            yield Button(
                "Rebuild + Deploy All",
                id="btn-rebuild-deploy",
                variant="warning",
            )
            yield Select(
                _BUILD_SCOPES,
                id="topo-build-scope",
                value="all",
            )
        yield LogStream(id="topo-progress", max_lines=200, flush_interval=0.15)

    def on_mount(self) -> None:
        table = self.query_one("#topo-table", DataTable)
        table.add_columns(
            ("Node", "node"),
            ("Role", "role"),
            ("Status", _STATUS_COL),
            ("Detail", "detail"),
        )
        table.cursor_type = "row"

    def set_workspace_root(self, path: Path) -> None:
        self._workspace_root = path

    # ── snapshot → table ──────────────────────────────────────────────────

    def update_from_snapshot(self, snapshot: TopologySnapshot) -> None:
        """Refresh the table rows from a topology snapshot."""
        table = self.query_one("#topo-table", DataTable)
        table.clear()

        m = snapshot.master
        table.add_row(
            m.stargate_id,
            "Master",
            _status_cell(m.status),
            f":{m.port}" + (f" PID {m.pid}" if m.pid else ""),
            key=_MASTER_ROW_KEY,
        )

        if snapshot.local_edge:
            e = snapshot.local_edge
            socket_name = e.socket.rsplit("/", 1)[-1] if e.socket else "UDS"
            detail = socket_name
            if e.container:
                detail += f" → {e.container}"
            table.add_row(
                e.stargate_id,
                "Edge (local)",
                _status_cell(e.status),
                detail,
                key="edge-localhost",
            )

        for r in snapshot.remotes:
            hostname = _hostname_from_stargate_id(r.stargate_id)
            models = f" ({r.model_count} models)" if r.model_count is not None else ""
            reason = _status_reason_suffix(r.status_reason)
            table.add_row(
                r.stargate_id,
                "Relay",
                _status_cell(r.status),
                f"{r.url}{models}{reason}",
                key=hostname,
            )

        has_remotes = bool(snapshot.remotes)
        self.query_one("#btn-deploy-all", Button).disabled = not has_remotes
        self.query_one("#btn-rebuild-deploy", Button).disabled = not has_remotes

    # ── node log switching ────────────────────────────────────────────────

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if not self._deploying or not event.row_key:
            return
        node_key = str(event.row_key.value)
        if node_key != self._active_node:
            self._switch_to_node(node_key)

    def _switch_to_node(self, node_key: str) -> None:
        self._active_node = node_key
        log = self.query_one("#topo-progress", LogStream)
        log.clear()
        for line in self._node_buffers.get(node_key, []):
            log.write_line(line)

    def _append_line(self, node_key: str, line: str) -> None:
        """Buffer a line for *node_key*; push to LogStream if it's the active node."""
        self._node_buffers.setdefault(node_key, []).append(line)
        if self._active_node == node_key:
            self.query_one("#topo-progress", LogStream).write_line(line)

    def _set_node_status(self, node_key: str, status_text: str) -> None:
        table = self.query_one("#topo-table", DataTable)
        try:
            table.update_cell(node_key, _STATUS_COL, status_text)
        except Exception:
            logger.exception("Could not update status cell for %s", node_key)

    # ── fleet operations ──────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn-deploy-all":
                self._run_fleet_operation(build=False)
            case "btn-rebuild-deploy":
                self._run_fleet_operation(build=True)

    def _selected_scope(self) -> str:
        val = self.query_one("#topo-build-scope", Select).value
        return str(val) if val != Select.BLANK else "all"

    def _run_fleet_operation(self, *, build: bool) -> None:
        if self._workspace_root is None or self._deploying:
            return
        svc = cast("ModelManagerApp", self.app).service_controller
        if build and svc.build_running:
            self._node_buffers.clear()
            self._active_node = _MASTER_ROW_KEY
            log = self.query_one("#topo-progress", LogStream)
            log.display = True
            log.clear()
            self._set_node_status(_MASTER_ROW_KEY, "⟳ build already running")
            self._append_line(
                _MASTER_ROW_KEY,
                "[localhost] Local image build already in progress.",
            )
            self._append_line(
                _MASTER_ROW_KEY,
                "[localhost] Wait for it to finish or cancel it from Services before running Rebuild + Deploy All again.",
            )
            return
        scope = self._selected_scope()
        self.run_worker(self._do_fleet_deploy(build=build, scope=scope), exclusive=True)

    async def _do_fleet_deploy(self, *, build: bool, scope: str) -> None:
        from ...controller.fleet import FleetOrchestrator

        svc = cast("ModelManagerApp", self.app).service_controller
        assert self._workspace_root is not None
        self._deploying = True
        self._deploy_run_id += 1
        deploy_run_id = self._deploy_run_id
        self._node_buffers.clear()
        self._active_node = _MASTER_ROW_KEY
        self.post_message(self.DeployStateChanged(deploying=True))

        log = self.query_one("#topo-progress", LogStream)
        log.display = True
        log.clear()

        orch = FleetOrchestrator(
            ctl=svc, root=self._workspace_root, sink=_WidgetFleetSink(self)
        )
        try:
            await orch.sync_restart_all(build=build, scope=scope)
        finally:
            self._deploying = False
            self.post_message(self.DeployStateChanged(deploying=False))
            self.set_timer(10, lambda: self._auto_hide_log(deploy_run_id))
            # Refresh UI state after workflow completes so the topology table
            # reflects restarted local services and any redeployed remotes.
            try:
                assert self._workspace_root is not None
                services = svc.service_state.check_all()
                snapshot = build_snapshot(self._workspace_root, services=services)
                self.update_from_snapshot(snapshot)
            except Exception:
                logger.exception("Failed to refresh topology snapshot after deploy")

    def _auto_hide_log(self, deploy_run_id: int) -> None:
        """Collapse the deploy log stream after idle timeout.

        Args:
            deploy_run_id: The ID of the deploy run that scheduled this hide operation.
                           Used to prevent stale timers from hiding new logs.
        """
        if not self._deploying and deploy_run_id == self._deploy_run_id:
            self.query_one("#topo-progress", LogStream).display = False


class _WidgetFleetSink:
    """Binds FleetProgressSink to the topology widget's buffer/table mutators."""

    def __init__(self, panel: TopologyPanel) -> None:
        self._panel = panel

    def line(self, node: str, text: str) -> None:
        self._panel._append_line(node, text)

    def status(self, node: str, text: str) -> None:
        self._panel._set_node_status(node, text)

    def focus(self, node: str) -> None:
        self._panel._switch_to_node(node)


# ── helpers ───────────────────────────────────────────────────────────────


_STATUS_DISPLAY: dict[str, str] = {
    "running": "● running",
    "stopped": "○ stopped",
    "unreachable": "◌ unreachable",
    "configured": "○ configured",
}


def _status_cell(status: str) -> str:
    """Map a raw status string to a display string with a Unicode bullet prefix."""
    return _STATUS_DISPLAY.get(status, f"? {status}")


def _status_reason_suffix(status_reason: str | None) -> str:
    """Render a readable status-reason suffix for the relay detail column."""
    if not status_reason:
        return ""
    reason_text = _STATUS_REASON_DISPLAY.get(status_reason, status_reason)
    return f" [{reason_text}]"
