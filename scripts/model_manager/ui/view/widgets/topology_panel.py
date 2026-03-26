"""Topology panel — live-probed node table with parallel fleet operations.

Nodes are displayed in a DataTable. During fleet deploys, each remote runs
in parallel via TaskGroup. Per-node output is buffered; clicking a row
switches the LogStream to that node's output.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
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

from scripts.model_manager.topology import TopologySnapshot, build_snapshot

from ...controller.operation_log import tee_with_summary
from ...controller.service_config import (
    is_agent_bus_configured,
    is_cloud_proxy_configured,
    is_cortex_configured,
    is_mcp_configured,
    is_rag_configured,
)
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
        svc = cast("ModelManagerApp", self.app).service_controller
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
                local_build_ok, remote_results = await self._parallel_build(scope)
                if local_build_ok:
                    await self._restart_local_services()
                else:
                    self._switch_to_node(_MASTER_ROW_KEY)
                    self._append_line(
                        _MASTER_ROW_KEY,
                        "⚠ Local build failed; skipped local restart.",
                    )
                for hostname, ok in remote_results.items():
                    if ok:
                        await self._verify_relay_connection(hostname)
            else:
                await self._deploy_remotes_parallel(build=False, scope=scope)
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

    async def _parallel_build(self, scope: str) -> tuple[bool, dict[str, bool]]:
        """Build images on localhost and all remotes in parallel.

        Args:
            scope: The build scope (e.g., 'all', 'llama').

        Returns local-build success and remote hostname → success mapping for deferred connection
        verification (remotes start their relay, but master may not be up yet).
        """
        remotes = list_remotes()
        targets = _parse_remote_targets(remotes)
        results: dict[str, bool] = {}

        self._set_node_status(_MASTER_ROW_KEY, "● running (build in progress)")
        for hostname, _ in targets:
            self._set_node_status(hostname, "⟳ building image...")

        async with asyncio.TaskGroup() as tg:
            local_build = tg.create_task(self._build_local_image(scope))
            for hostname, address in targets:
                tg.create_task(
                    self._deploy_and_build_remote(
                        hostname=hostname,
                        address=address,
                        scope=scope,
                        results=results,
                    )
                )

        return local_build.result(), results

    async def _build_local_image(self, scope: str) -> bool:
        """Build the Docker image locally (no service restart).

        Args:
            scope: The build scope (e.g., 'all', 'llama').

        Returns: True if the build was successful, False otherwise.
        """
        svc = cast("ModelManagerApp", self.app).service_controller
        mk = _MASTER_ROW_KEY
        self._set_node_status(mk, "● running (build in progress)")
        self._append_line(mk, f"Building image (scope={scope})...")
        summary = tee_with_summary(
            svc.build_image(scope=scope),
            operation="build",
            host=mk,
        )
        # Be robust to log-shape changes: if we see an explicit failure marker,
        # treat it as failed; otherwise assume success and proceed to restart.
        build_ok = True
        saw_success_marker = False
        async for line in summary:
            self._append_line(mk, line)
            if _build_summary_failed(line):
                build_ok = False
            if "Build completed successfully." in line:
                saw_success_marker = True
                build_ok = True
        if build_ok or saw_success_marker:
            self._set_node_status(mk, "○ built image, restart pending")
            return True
        self._set_node_status(mk, "✗ build failed")
        return False

    async def _restart_local_services(self) -> None:
        """Restart local services with best-effort phased orchestration.

        ∀ stop operations: best-effort, never abort the restart flow.
        ∀ start operations: try/except wrapped, logged, never abort.
        Critical failures (event_service/gateway/stargate/agent_bus) are surfaced
        distinctly from optional-service failures.
        """
        svc = cast("ModelManagerApp", self.app).service_controller
        mk = _MASTER_ROW_KEY
        assert self._workspace_root is not None
        ws_root = self._workspace_root
        failures: list[str] = []

        self._node_buffers[mk] = []
        self._switch_to_node(mk)
        self._set_node_status(mk, "⟳ restarting...")
        self._append_line(mk, "Restarting services...")

        # Phase 1: Stop all (best-effort, parallel)
        stop_ops: list[tuple[str, Callable[[], Awaitable[str]]]] = [
            ("gateway", svc.stop_gateway),
            ("stargate", svc.stop_stargate),
            ("sidecar", svc.sidecar.stop),
        ]
        if is_rag_configured():
            stop_ops.append(("rag", svc.stop_rag))
        if is_cloud_proxy_configured():
            stop_ops.append(("cloud_proxy", svc.stop_cloud_proxy))
        if is_mcp_configured(ws_root):
            stop_ops.append(("mcp", svc.stop_mcp))
        if is_cortex_configured():
            stop_ops.append(("cortex_api", svc.stop_cortex_api))
        if is_agent_bus_configured():
            stop_ops.append(("agent_bus", svc.stop_agent_bus))

        stop_results = await self._run_ops_parallel(stop_ops)
        for name, ok, msg in stop_results:
            self._append_line(mk, f"  {'✓' if ok else '⚠'} stop {name}")
            if not ok:
                logger.warning("stop %s: %s", name, msg)

        # Phase 2: Event service (observability backbone)
        ev_ok = await self._run_single(
            mk, "event_service", svc.rebuild_event_service, failures
        )
        if ev_ok:
            if not await svc.wait_healthy_event_service(timeout=30):
                self._append_line(mk, "  ⚠ event_service unhealthy (continuing)")

        # Phase 3: Critical local services
        await self._run_single(mk, "gateway", svc.start_gateway, failures)
        if is_agent_bus_configured():
            await self._run_single(mk, "agent_bus", svc.rebuild_agent_bus, failures)

        # Phase 4: Stargate + optional services (parallel, best-effort)
        start_ops: list[tuple[str, Callable[[], Awaitable[str]]]] = [
            ("stargate", svc.start_stargate),
        ]
        if is_rag_configured():
            start_ops.append(("rag", svc.start_rag))
        if is_cloud_proxy_configured():
            start_ops.append(("cloud_proxy", svc.start_cloud_proxy))
        if is_mcp_configured(ws_root):
            start_ops.append(("mcp", svc.rebuild_mcp))
        if is_cortex_configured():
            start_ops.append(("cortex_api", svc.rebuild_cortex_api))

        start_results = await self._run_ops_parallel(start_ops)
        for name, ok, msg in start_results:
            self._append_line(mk, f"  {'✓' if ok else '✗'} {name}")
            if not ok:
                failures.append(name)

        self._append_line(mk, "  ○ sidecar left stopped")

        if not failures:
            self._append_line(mk, "Done — required services started")
            self._set_node_status(mk, "● running")
        else:
            self._append_line(mk, f"Done — failed: {', '.join(failures)}")
            core_failed = any(
                f in ("event_service", "gateway", "stargate", "agent_bus")
                for f in failures
            )
            status = "✗ core start failed" if core_failed else "◌ partial"
            self._set_node_status(mk, status)

    async def _run_ops_parallel(
        self,
        ops: list[tuple[str, Callable[[], Awaitable[str]]]],
    ) -> list[tuple[str, bool, str]]:
        """Run service operations in parallel. Each op is try/except wrapped — never raises."""

        async def _safe_run(
            name: str, op: Callable[[], Awaitable[str]]
        ) -> tuple[str, bool, str]:
            try:
                msg = await op()
                return name, _classify_result(msg), msg
            except Exception as exc:
                logger.exception("Service op %s raised", name)
                return name, False, str(exc)

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_safe_run(n, op)) for n, op in ops]
        return [t.result() for t in tasks]

    async def _run_single(
        self,
        node_key: str,
        name: str,
        op: Callable[[], Awaitable[str]],
        failures: list[str],
    ) -> bool:
        """Run one operation, log result, append to failures if not ok."""
        try:
            msg = await op()
            ok = _classify_result(msg)
        except Exception as exc:
            logger.exception("Service op %s raised", name)
            ok, msg = False, str(exc)
        self._append_line(node_key, f"  {'✓' if ok else '✗'} {name}")
        if not ok:
            failures.append(name)
            logger.warning("%s: %s", name, msg)
        return ok

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
        try:
            async for line in summary:
                self._append_line(hostname, line)
                if "[red]" in line:
                    failed = True
        except Exception as e:
            self._append_line(hostname, f"Error during remote deploy: {e}")
            logger.exception("Error deploying remote %s", hostname)
            failed = True

        if failed:
            self._set_node_status(hostname, "✗ failed")
            self._append_line(hostname, f"--- {hostname}: ✗ failed ---")
            results[hostname] = False
        else:
            self._set_node_status(hostname, "✓ built")
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
        """Deploy all remotes in parallel via TaskGroup.

        Args:
            build: Whether to build the image on the remote before deploying.
            scope: The build scope (e.g., 'all', 'llama').
        """
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
        """Deploy one remote, buffering output to its node key.

        Args:
            hostname: The hostname of the remote node.
            address: The network address of the remote node.
            build: Whether to build the image on the remote before deploying.
            scope: The build scope (e.g., 'all', 'llama').
        """
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
        try:
            async for line in summary:
                self._append_line(hostname, line)
                if "[red]" in line:
                    failed = True
        except Exception as e:
            self._append_line(hostname, f"Error during remote deploy: {e}")
            logger.exception("Error deploying remote %s", hostname)
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


def _classify_result(msg: str) -> bool:
    """Return True if service operation succeeded or was a no-op."""
    lower = msg.strip().lower()
    if any(
        lower.startswith(p)
        for p in (
            "gateway container started",
            "gateway stopped",
            "gateway is not running",
            "stargate starting",
            "stargate stopped",
            "stargate is not running",
            "stargate already exited",
            "event service started",
            "event service stopped",
            "mcp server started",
            "mcp server stopped",
            "cortex api started",
            "cortex api stopped",
            "agent bus started",
            "agent bus stopped",
            "sidecar",
        )
    ):
        return True
    if any(
        k in lower
        for k in (
            "starting (pid",
            "is not running",
            "is already running",
            "stopped (pid",
            "already exited",
        )
    ):
        return True
    return False


def _build_summary_failed(line: str) -> bool:
    """Classify summarized build lines that should block restart."""
    text = line.strip()
    return (
        "Build FAILED" in text
        or "Build cancelled" in text
        or "ERROR:" in text
        or "[red]" in text
    )
