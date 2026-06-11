"""Local-node phase helpers for fleet orchestration (free functions, no Textual).

Split out of fleet.py to keep each module ≤300 SLOC. Every function emits
progress through an injected FleetProgressSink and takes the ServiceController
(and workspace *root* where needed) explicitly — no view state. Lifted verbatim
from view/widgets/topology_panel.py with the sink/ctl/root substitution recipe.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

from scripts.model_manager.observation_event import (
    emit_fleet_service_phase,
    emit_fleet_service_step,
)

from .fleet_remote import (
    _MASTER_ROW_KEY,
    _build_summary_failed,
    _classify_result,
    _parse_remote_targets,
    deploy_and_build_remote,
)
from .operation_log import tee_with_summary
from .service_config import (
    is_agent_bus_configured,
    is_cloud_proxy_configured,
    is_cortex_configured,
    is_mcp_configured,
    is_rag_configured,
)
from .topology import list_remotes

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from .fleet import FleetProgressSink
    from .service_ctl.core import ServiceController

logger = get_logger(__name__)


async def wait_event_service_healthy(
    ctl: ServiceController, *, timeout: float = 30.0
) -> bool:
    """Poll the current event-service UDS health check until healthy or timed out."""
    from ..model.service_state import ServiceStatus

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        info = await asyncio.to_thread(ctl.service_state.check_event_service)
        if info.status is ServiceStatus.RUNNING:
            return True
        await asyncio.sleep(1.0)
    return False


async def run_ops_parallel(
    ops: list[tuple[str, Callable[[], Awaitable[str]]]],
) -> list[tuple[str, bool, str, float]]:
    """Run service operations in parallel. Each op is try/except wrapped — never raises.

    Returns (name, success, message, duration_s) per operation so callers can
    surface per-service timing in the TUI log and emit fleet.service.step events.
    """

    async def _safe_run(
        name: str, op: Callable[[], Awaitable[str]]
    ) -> tuple[str, bool, str, float]:
        t0 = asyncio.get_running_loop().time()
        try:
            msg = await op()
            return (
                name,
                _classify_result(msg),
                msg,
                asyncio.get_running_loop().time() - t0,
            )
        except Exception as exc:
            logger.exception("Service op %s raised", name)
            return name, False, str(exc), asyncio.get_running_loop().time() - t0

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(_safe_run(n, op)) for n, op in ops]
    return [t.result() for t in tasks]


async def run_single(
    sink: FleetProgressSink,
    node_key: str,
    name: str,
    op: Callable[[], Awaitable[str]],
    failures: list[str],
    *,
    phase: str | None = None,
) -> bool:
    """Run one operation, log result, append to failures if not ok.

    When *phase* is provided, emits a ``fleet.service.step`` event with the
    per-service duration so bottlenecks are visible in the event service.
    """
    t0 = asyncio.get_running_loop().time()
    try:
        msg = await op()
        ok = _classify_result(msg)
    except Exception as exc:
        logger.exception("Service op %s raised", name)
        ok, msg = False, str(exc)
    elapsed = asyncio.get_running_loop().time() - t0
    sink.line(node_key, f"  {'✓' if ok else '✗'} {name} ({elapsed:.1f}s)")
    if not ok:
        failures.append(name)
        logger.warning("%s: %s", name, msg)
    if phase is not None:
        await emit_fleet_service_step(
            phase=phase, service=name, success=ok, duration_s=elapsed
        )
    return ok


async def build_local_image(
    ctl: ServiceController, sink: FleetProgressSink, scope: str
) -> bool:
    """Build the Docker image locally (no service restart)."""
    mk = _MASTER_ROW_KEY
    sink.status(mk, "● running (build in progress)")
    sink.line(mk, f"Building image (scope={scope})...")
    summary = tee_with_summary(
        ctl.build_image(scope=scope, no_cache=True),
        operation="build",
        host=mk,
    )
    # Be robust to log-shape changes: if we see an explicit failure marker,
    # treat it as failed; otherwise assume success and proceed to restart.
    build_ok = True
    saw_success_marker = False
    async for line in summary:
        sink.line(mk, line)
        if _build_summary_failed(line):
            build_ok = False
        if "Build completed successfully." in line:
            saw_success_marker = True
            build_ok = True
    if build_ok or saw_success_marker:
        sink.status(mk, "○ built image, restart pending")
        return True
    sink.status(mk, "✗ build failed")
    return False


async def parallel_build(
    ctl: ServiceController, root: Path, sink: FleetProgressSink, scope: str
) -> tuple[bool, dict[str, bool]]:
    """Build images on localhost and all remotes in parallel.

    Returns local-build success and remote hostname → success mapping for deferred
    connection verification (remotes start their relay, but master may not be up yet).
    """
    targets = _parse_remote_targets(list_remotes())
    results: dict[str, bool] = {}

    sink.status(_MASTER_ROW_KEY, "● running (build in progress)")
    for hostname, _ in targets:
        sink.status(hostname, "⟳ building image...")

    async with asyncio.TaskGroup() as tg:
        local_build = tg.create_task(build_local_image(ctl, sink, scope))
        for hostname, address in targets:
            tg.create_task(
                deploy_and_build_remote(
                    hostname=hostname,
                    address=address,
                    scope=scope,
                    results=results,
                    sink=sink,
                    root=root,
                )
            )

    return local_build.result(), results


async def stop_local_services(
    ctl: ServiceController, sink: FleetProgressSink
) -> list[str]:
    """Stop local services in parallel and return any failures."""
    mk = _MASTER_ROW_KEY

    stop_ops: list[tuple[str, Callable[[], Awaitable[str]]]] = [
        ("gateway", ctl.stop_gateway),
        ("stargate", ctl.stop_stargate),
        ("sidecar", ctl.sidecar.stop),
    ]
    if is_rag_configured():
        stop_ops.append(("rag", ctl.stop_rag))
    if is_cloud_proxy_configured():
        stop_ops.append(("cloud_proxy", ctl.stop_cloud_proxy))
    # MCP is intentionally excluded from the stop phase: start_mcp /
    # sync_and_restart_mcp docker-cp-syncs into the running container then
    # graceful-restarts in-place, keeping downtime to the few-second swap window.
    if is_cortex_configured():
        stop_ops.append(("cortex_api", ctl.stop_cortex_api))
    if is_agent_bus_configured():
        stop_ops.append(("agent_bus", ctl.stop_agent_bus))

    stop_results = await run_ops_parallel(stop_ops)
    stop_dict: dict[str, bool] = {}
    failures: list[str] = []
    for name, ok, msg, elapsed in stop_results:
        stop_dict[name] = ok
        sink.line(mk, f"  {'✓' if ok else '⚠'} stop {name} ({elapsed:.1f}s)")
        if not ok:
            failures.append(name)
            logger.warning("stop %s: %s", name, msg)
    await emit_fleet_service_phase(
        phase="stop",
        services=[n for n, _, _, _ in stop_results],
        results=stop_dict,
    )
    for name, ok, _, elapsed in stop_results:
        await emit_fleet_service_step(
            phase="stop", service=name, success=ok, duration_s=elapsed
        )
    return failures


def _build_start_ops(
    ctl: ServiceController, ws_root: Path, *, rebuild_supporting_services: bool
) -> list[tuple[str, Callable[[], Awaitable[str]]]]:
    """Assemble the conditional per-service start operation list."""
    start_ops: list[tuple[str, Callable[[], Awaitable[str]]]] = [
        ("gateway", ctl.start_gateway),
        ("stargate", ctl.start_stargate),
    ]
    if is_agent_bus_configured():
        agent_bus_op = (
            ctl.rebuild_agent_bus
            if rebuild_supporting_services
            else ctl.start_agent_bus
        )
        start_ops.append(("agent_bus", agent_bus_op))
    if is_cloud_proxy_configured():
        start_ops.append(("cloud_proxy", ctl.start_cloud_proxy))
    if is_mcp_configured(ws_root):
        # Canonical deploy: mcp_service.sync_and_restart_mcp (same as TUI Sync+Start MCP).
        # rebuild_deploy: full --no-cache image rebuild (pip/Dockerfile changes only).
        mcp_op: Callable[[], Awaitable[str]] = (
            (lambda: ctl.sync_restart_mcp(no_cache=True))
            if rebuild_supporting_services
            else ctl.start_mcp
        )
        start_ops.append(("mcp", mcp_op))
    if is_cortex_configured():
        cortex_api_op = (
            ctl.rebuild_cortex_api
            if rebuild_supporting_services
            else ctl.start_cortex_api
        )
        start_ops.append(("cortex_api", cortex_api_op))
    if is_rag_configured():
        start_ops.append(("rag", ctl.start_rag))
    return start_ops


async def restart_local_services(
    ctl: ServiceController,
    root: Path,
    sink: FleetProgressSink,
    *,
    rebuild_supporting_services: bool,
    already_stopped: bool = False,
) -> bool:
    """Restart local services with best-effort phased orchestration.

    Event service runs first (observability backbone that can block). All
    other services (gateway, stargate, agent_bus, rag, mcp, etc.) run in
    parallel via TaskGroup. ∀ start operations: try/except wrapped, logged,
    never abort. Critical failures (event_service/gateway/stargate/agent_bus)
    are surfaced distinctly from optional-service failures.
    """
    mk = _MASTER_ROW_KEY
    failures: list[str] = []

    sink.focus(mk)
    sink.status(mk, "⟳ restarting...")
    sink.line(mk, "Restarting services...")

    # Phase 1: Stop all (best-effort, parallel)
    if not already_stopped:
        await stop_local_services(ctl, sink)

    # Phase 2: Event service (observability backbone — blocks parallel starts)
    event_service_op = (
        ctl.rebuild_event_service
        if rebuild_supporting_services
        else ctl.restart_event_service
    )
    ev_ok = await run_single(
        sink, mk, "event_service", event_service_op, failures, phase="start"
    )
    if ev_ok and not await wait_event_service_healthy(ctl, timeout=30):
        sink.line(mk, "  ⚠ event_service unhealthy (continuing)")

    # Phase 3: All other services (parallel, best-effort). Event service is the
    # only intentional sequential blocker per topology policy; gateway/agent_bus/
    # stargate/cloud_proxy/mcp/cortex_api/rag now run concurrently. RAG
    # activation retries until nodes have registered models/telemetry.
    start_ops = _build_start_ops(
        ctl, root, rebuild_supporting_services=rebuild_supporting_services
    )

    start_results = await run_ops_parallel(start_ops)
    start_dict: dict[str, bool] = {}
    for name, ok, _msg, elapsed in start_results:
        start_dict[name] = ok
        sink.line(mk, f"  {'✓' if ok else '✗'} {name} ({elapsed:.1f}s)")
        if not ok:
            failures.append(name)
    await emit_fleet_service_phase(
        phase="start",
        services=[n for n, _, _, _ in start_results],
        results=start_dict,
    )
    for name, ok, _, elapsed in start_results:
        await emit_fleet_service_step(
            phase="start", service=name, success=ok, duration_s=elapsed
        )

    sink.line(mk, "  ○ sidecar left stopped")

    if not failures:
        sink.line(mk, "Done — required services started")
        sink.status(mk, "● running")
        return True

    sink.line(mk, f"Done — failed: {', '.join(failures)}")
    core_failed = any(
        f in ("event_service", "gateway", "stargate", "agent_bus") for f in failures
    )
    status = "✗ core start failed" if core_failed else "◌ partial"
    sink.status(mk, status)
    return not core_failed
