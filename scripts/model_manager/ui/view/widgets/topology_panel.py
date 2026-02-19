"""Topology panel — live-probed node table with parallel fleet operations.

Nodes are displayed in a DataTable. During fleet deploys, each remote runs
in parallel via TaskGroup. Per-node output is buffered; clicking a row
switches the LogStream to that node's output.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, DataTable, Select, Static

from scripts.model_manager.topology import TopologySnapshot

from ...controller.operation_log import tee_with_summary
from ...controller.topology import deploy_remote, list_remotes, wait_for_relay_connected
from .log_stream import LogStream

logger = logging.getLogger(__name__)

_STATUS_COL = "status"


class TopologyPanel(Widget):
    """DataTable showing all nodes with live status + parallel fleet operations."""

    class DeployStateChanged(Message):
        """Posted when a fleet deploy starts or finishes."""

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

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._workspace_root: Path | None = None
        self._node_buffers: dict[str, list[str]] = {}
        self._active_node: str | None = None
        self._deploying: bool = False

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
                [("vLLM + llama", "all"), ("llama only", "llama")],
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
            key="localhost",
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
            table.add_row(
                r.stargate_id,
                "Relay",
                _status_cell(r.status),
                f"{r.url}{models}",
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
            pass

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
        self._node_buffers.clear()
        self._active_node = "localhost"
        self.post_message(self.DeployStateChanged(deploying=True))

        log = self.query_one("#topo-progress", LogStream)
        log.display = True
        log.clear()

        try:
            if build:
                await self._build_local(scope)

            assert self._workspace_root is not None
            await self._deploy_remotes_parallel(build=build, scope=scope)
        finally:
            self._deploying = False
            self.post_message(self.DeployStateChanged(deploying=False))
            self.set_timer(10, self._auto_hide_log)

    def _auto_hide_log(self) -> None:
        """Collapse the deploy log stream after idle timeout."""
        if not self._deploying:
            self.query_one("#topo-progress", LogStream).display = False

    async def _build_local(self, scope: str) -> None:
        """Build image + restart local services (sequential)."""
        svc = self.app.service_controller  # type: ignore[attr-defined]
        self._set_node_status("localhost", "⟳ building...")

        self._append_line("localhost", f"Building image (scope={scope})...")
        summary = tee_with_summary(
            svc.build_image(scope=scope),
            operation="build",
            host="localhost",
        )
        async for line in summary:
            self._append_line("localhost", line)

        self._append_line("localhost", "Restarting local services...")
        self._append_line("localhost", await svc.stop_stargate())
        self._append_line("localhost", await svc.stop_gateway())
        await asyncio.sleep(1)
        self._append_line("localhost", await svc.start_gateway())
        await asyncio.sleep(2)
        self._append_line("localhost", await svc.start_stargate())
        self._set_node_status("localhost", "● running")

    async def _deploy_remotes_parallel(self, *, build: bool, scope: str) -> None:
        """Deploy all remotes in parallel via TaskGroup."""
        remotes = list_remotes()
        if not remotes:
            self._append_line("localhost", "No remotes configured.")
            return

        targets = _parse_remote_targets(remotes)
        for hostname, _ in targets:
            self._set_node_status(hostname, "⟳ deploying...")

        if targets:
            self._switch_to_node(targets[0][0])

        async with asyncio.TaskGroup() as tg:
            for hostname, address in targets:
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
        remote_id = f"relay-{hostname}"
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
            if "failed" in line.lower():
                failed = True

        if failed:
            self._set_node_status(hostname, "✗ failed")
            self._append_line(hostname, f"--- {hostname}: ✗ failed ---")
            return

        self._set_node_status(hostname, "⟳ connecting...")
        self._append_line(
            hostname, f"[{hostname}] Waiting for relay to register with master..."
        )
        connected = await wait_for_relay_connected(remote_id)
        status = "● connected" if connected else "◌ unreachable"
        self._set_node_status(hostname, status)
        self._append_line(hostname, f"--- {hostname}: {status} ---")


# ── helpers ───────────────────────────────────────────────────────────────


def _hostname_from_stargate_id(stargate_id: str) -> str:
    return stargate_id.removeprefix("relay-")


def _parse_remote_targets(
    remotes: list[dict[str, object]],
) -> list[tuple[str, str]]:
    """Extract (hostname, address) pairs from remote config dicts."""
    targets: list[tuple[str, str]] = []
    for remote in remotes:
        sid = str(remote.get("stargate_id", "?"))
        hostname = _hostname_from_stargate_id(sid)
        url = str(remote.get("url", ""))
        try:
            address = urlparse(url).hostname or ""
        except Exception:
            address = ""
        if address:
            targets.append((hostname, address))
        else:
            logger.warning("Cannot parse address for %s — skipping", sid)
    return targets


def _status_cell(status: str) -> str:
    match status:
        case "running":
            return "● running"
        case "stopped":
            return "○ stopped"
        case "unreachable":
            return "◌ unreachable"
        case "configured":
            return "○ configured"
        case _:
            return f"? {status}"
