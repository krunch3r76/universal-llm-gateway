"""Topology panel — live-probed node table with parallel fleet operations.

Nodes are displayed in a DataTable. During fleet deploys, each remote runs
in parallel via TaskGroup. Per-node output is buffered; clicking a row
switches the LogStream to that node's output.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, DataTable, Select, Static

if TYPE_CHECKING:
    from ...app import ModelManagerApp

from scripts.model_manager.topology import TopologySnapshot

from ...controller.operation_log import tee_with_summary
from ...controller.service_config import is_mcp_configured
from ...controller.topology import deploy_remote, list_remotes, wait_for_relay_connected
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
}


class TopologyPanel(Widget):
    """DataTable showing all nodes with live status + parallel fleet operations."""

    class DeployStateChanged(Message):
        """Posted when a fleet deploy starts or finishes.

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
            yield Button("Deploy All Remotes", id="btn-deploy-all", variant="warning")
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
        if self._deploying:
            return
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
        scope = self._selected_scope()
        self.run_worker(self._do_fleet_deploy(build=build, scope=scope), exclusive=True)

    async def _do_fleet_deploy(self, *, build: bool, scope: str) -> None:
        self._deploying = True
        self._deploy_run_id += 1
        deploy_run_id = self._deploy_run_id
        self._node_buffers.clear()
        self._active_node = _MASTER_ROW_KEY
        self.post_message(self.DeployStateChanged(deploying=True))

        log = self.query_one("#topo-progress", LogStream)
        log.display = True
        log.clear()

        try:
            assert self._workspace_root is not None
            if build:
                remote_results = await self._parallel_build(scope)
                await self._restart_local_services()
                for hostname, ok in remote_results.items():
                    if ok:
                        await self._verify_relay_connection(hostname)
            else:
                await self._deploy_remotes_parallel(build=False, scope=scope)
        finally:
            self._deploying = False
            self.post_message(self.DeployStateChanged(deploying=False))
            self.set_timer(10, lambda: self._auto_hide_log(deploy_run_id))

    def _auto_hide_log(self, deploy_run_id: int) -> None:
        """Collapse the deploy log stream after idle timeout."""
        if not self._deploying and deploy_run_id == self._deploy_run_id:
            self.query_one("#topo-progress", LogStream).display = False

    async def _parallel_build(self, scope: str) -> dict[str, bool]:
        """Build images on localhost and all remotes in parallel.

        Returns remote hostname → success mapping for deferred connection
        verification (remotes start their relay, but master may not be up yet).
        """
        remotes = list_remotes()
        targets = _parse_remote_targets(remotes)
        results: dict[str, bool] = {}

        self._set_node_status(_MASTER_ROW_KEY, "⟳ building...")
        for hostname, _ in targets:
            self._set_node_status(hostname, "⟳ building...")

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._build_local_image(scope))
            for hostname, address in targets:
                tg.create_task(
                    self._deploy_and_build_remote(
                        hostname=hostname,
                        address=address,
                        scope=scope,
                        results=results,
                    )
                )

        return results

    async def _build_local_image(self, scope: str) -> None:
        """Build the Docker image locally (no service restart)."""
        svc = cast("ModelManagerApp", self.app).service_controller
        mk = _MASTER_ROW_KEY
        self._append_line(mk, f"Building image (scope={scope})...")
        summary = tee_with_summary(
            svc.build_image(scope=scope),
            operation="build",
            host=mk,
        )
        async for line in summary:
            self._append_line(mk, line)

    async def _restart_local_services(self) -> None:
        """Restart local services after image build completes."""
        svc = cast("ModelManagerApp", self.app).service_controller
        mk = _MASTER_ROW_KEY
        self._switch_to_node(mk)
        self._set_node_status(mk, "⟳ restarting...")

        self._append_line(mk, "Restarting event service (triggers retention prune)...")
        result = await svc.restart_event_service()
        self._append_line(mk, result)
        if _service_operation_failed(result):
            self._set_node_status(mk, "✗ event service failed")
            self._append_line(mk, "⚠ event_service restart failed; aborting.")
            return

        self._append_line(mk, "Waiting for event service...")
        healthy = await svc.wait_healthy_event_service(timeout=30)
        if not healthy:
            self._set_node_status(mk, "✗ event service unhealthy")
            self._append_line(mk, "⚠ event_service did not become healthy; aborting.")
            return
        self._append_line(mk, "Event service healthy. Proceeding with restart.")

        self._append_line(mk, "Restarting local services...")
        stop_ops: list[tuple[str, Any]] = [
            ("stop stargate", svc.stop_stargate),
            ("stop rag", svc.stop_rag),
            ("stop cloud_proxy", svc.stop_cloud_proxy),
            ("stop gateway", svc.stop_gateway),
        ]
        for label, op in stop_ops:
            result = await op()
            self._append_line(mk, result)
            if _service_operation_failed(result):
                self._set_node_status(mk, "✗ local restart failed")
                self._append_line(mk, f"⚠ {label} failed; aborting restart.")
                return

        await asyncio.sleep(1)
        start_ops: list[tuple[str, Any]] = [
            ("start gateway", svc.start_gateway),
            ("start rag", svc.start_rag),
            ("start cloud_proxy", svc.start_cloud_proxy),
            ("start stargate", svc.start_stargate),
        ]
        for idx, (label, op) in enumerate(start_ops):
            result = await op()
            self._append_line(mk, result)
            if label == "start stargate":
                failed = not result.startswith("Stargate starting")
            else:
                failed = _service_operation_failed(result)
            if failed:
                self._set_node_status(mk, "✗ local restart failed")
                self._append_line(mk, f"⚠ {label} failed; aborting restart.")
                return
            if idx == 0:
                await asyncio.sleep(0.5)
        self._set_node_status(mk, "● running")

        assert self._workspace_root is not None
        if is_mcp_configured(self._workspace_root):
            self._append_line(mk, "Rebuilding MCP server...")
            self._append_line(mk, await svc.rebuild_mcp())
        else:
            self._append_line(
                mk, "MCP skipped (not configured in ~/.gateway/mcp.yaml)."
            )

    async def _deploy_and_build_remote(
        self,
        *,
        hostname: str,
        address: str,
        scope: str,
        results: dict[str, bool],
    ) -> None:
        """Deploy and build on a remote node (connection verification deferred)."""
        assert self._workspace_root is not None
        raw: AsyncIterator[str] = deploy_remote(
            hostname=hostname,
            address=address,
            workspace_root=self._workspace_root,
            build=True,
            restart=True,
            scope=scope,
        )
        summary = tee_with_summary(raw, operation="deploy", host=hostname)
        failed = False
        async for line in summary:
            self._append_line(hostname, line)
            if "[red]" in line:
                failed = True

        if failed:
            self._set_node_status(hostname, "✗ failed")
            self._append_line(hostname, f"--- {hostname}: ✗ failed ---")
            results[hostname] = False
        else:
            results[hostname] = True

    async def _verify_relay_connection(self, hostname: str) -> None:
        """Verify a relay registered with master after deploy."""
        remote_id = f"relay-{hostname}"
        self._set_node_status(hostname, "⟳ connecting...")
        self._append_line(
            hostname, f"[{hostname}] Waiting for relay to register with master..."
        )
        result = await wait_for_relay_connected(remote_id)
        status = "● connected" if result.connected else "◌ unreachable"
        self._set_node_status(hostname, status)
        if not result.connected and result.reason:
            self._append_line(hostname, f"  reason: {result.reason}")
        if not result.connected:
            self._append_line(hostname, "  relay did not register in time")
            self._append_line(
                hostname,
                "  check: SSH_USER, ~/.gateway/nodes/<host>.env, FEDERATION_KEY_RELAY",
            )
            self._append_line(
                hostname,
                "  check: remote ./manage relay --restart output for auth/connection errors",
            )
        self._append_line(hostname, f"--- {hostname}: {status} ---")

    async def _deploy_remotes_parallel(self, *, build: bool, scope: str) -> None:
        """Deploy all remotes in parallel via TaskGroup."""
        remotes = list_remotes()
        if not remotes:
            self._append_line(_MASTER_ROW_KEY, "No remotes configured.")
            return

        targets = _parse_remote_targets(remotes)
        first = True
        async with asyncio.TaskGroup() as tg:
            for hostname, address in targets:
                self._set_node_status(hostname, "⟳ deploying...")
                if first:
                    self._switch_to_node(hostname)
                    first = False
                tg.create_task(
                    self._deploy_single_remote(
                        hostname=hostname,
                        address=address,
                        build=build,
                        scope=scope,
                    )
                )

    async def _deploy_single_remote(
        self,
        *,
        hostname: str,
        address: str,
        build: bool,
        scope: str,
    ) -> None:
        """Deploy one remote, buffering output to its node key."""
        assert self._workspace_root is not None
        raw: AsyncIterator[str] = deploy_remote(
            hostname=hostname,
            address=address,
            workspace_root=self._workspace_root,
            build=build,
            restart=True,
            scope=scope,
        )
        summary = tee_with_summary(raw, operation="deploy", host=hostname)
        failed = False
        async for line in summary:
            self._append_line(hostname, line)
            if "[red]" in line:
                failed = True

        if failed:
            self._set_node_status(hostname, "✗ failed")
            self._append_line(hostname, f"--- {hostname}: ✗ failed ---")
            return

        await self._verify_relay_connection(hostname)


# ── helpers ───────────────────────────────────────────────────────────────


def _hostname_from_stargate_id(stargate_id: str) -> str:
    """Derive the bare hostname from a relay stargate_id (strips 'relay-' prefix)."""
    return stargate_id.removeprefix("relay-")


def _parse_remote_targets(
    remotes: Sequence[Mapping[str, Any]],
) -> list[tuple[str, str]]:
    """Extract (hostname, address) pairs from remote config dicts."""
    targets: list[tuple[str, str]] = []
    for remote in remotes:
        sid = remote.get("stargate_id", "?")
        if not isinstance(sid, str):
            sid = str(sid)
        hostname = _hostname_from_stargate_id(sid)
        url = remote.get("url", "")
        if not isinstance(url, str):
            url = str(url)
        try:
            address = urlparse(url).hostname or ""
        except Exception:
            address = ""
        if address:
            targets.append((hostname, address))
        else:
            logger.warning("Cannot parse address for %s — skipping", sid)
    return targets


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


def _service_operation_failed(result: str) -> bool:
    """Classify service-controller operation output as success/failure."""
    text = result.strip().lower()
    return (
        text.startswith("failed")
        or text.startswith("error")
        or " failed " in text
        or " not found" in text
    )
