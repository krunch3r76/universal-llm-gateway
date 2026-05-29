"""Manage API dispatch helpers — JSON-RPC method routing to ServiceController.

Module-level helpers split from `api_server.py` so the connection / lifecycle
class can stay under SLOC ceiling and so dispatch logic remains testable in
isolation. Imported by `api_server._dispatch`.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from .controller.restart_drain import run_gated

if TYPE_CHECKING:
    from .controller.service_ctl.core import ServiceController
    from .model.service_state import ServiceInfo, ServiceState

# Service whitelists — kept local to dispatch to avoid cross-module mutation.
VALID_SERVICES = frozenset(
    {
        "gateway",
        "stargate",
        "rag",
        "cloud_proxy",
        "mcp",
        "event_service",
        "cortex_api",
        "agent_bus",
        "grokbuild_worker",
        "email_bridge",
    }
)
REBUILD_SERVICES = frozenset(
    {
        "mcp",
        "event_service",
        "cortex_api",
        "agent_bus",
        "grokbuild_worker",
        "email_bridge",
    }
)
SYNC_RESTART_SERVICES = frozenset(
    {
        "gateway",
        "mcp",
        "stargate",
        "rag",
        "cloud_proxy",
        "cortex_api",
        "agent_bus",
        "grokbuild_worker",
        "event_service",
    }
)


async def execute(
    ctl: ServiceController, method: str, service: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Dispatch a validated JSON-RPC method to ServiceController."""
    svc = ctl.service_state

    match method:
        case "status":
            infos = await asyncio.to_thread(svc.check_all)
            return {
                "services": {
                    i.name.lower().replace(" ", "_"): i.status.value for i in infos
                },
                "build": await asyncio.to_thread(_gateway_build_status, ctl),
            }

        case "health":
            require_service(service)
            info = await asyncio.to_thread(_check_one, svc, service)
            result = {
                "service": service,
                "status": info.status.value,
                "detail": info.detail,
            }
            if service == "gateway":
                result["build"] = await asyncio.to_thread(_gateway_build_status, ctl)
            return result

        case "wait_healthy":
            require_service(service)
            timeout = float(params.get("timeout", 120.0))
            waited = await _wait_healthy(svc, service, timeout)
            return {"healthy": True, "waited_s": round(waited, 1)}

        case "start":
            require_service(service)
            msg = await _start(ctl, service)
            return {"status": "ok", "message": msg}

        case "stop":
            require_service(service)
            return await run_gated(
                ctl.restart_gate,
                "stop",
                service,
                force=bool(params.get("force", False)),
                lifecycle=lambda: _stop(ctl, service),
            )

        case "restart":
            require_service(service)
            return await run_gated(
                ctl.restart_gate,
                "restart",
                service,
                force=bool(params.get("force", False)),
                lifecycle=lambda: _restart_cycle(ctl, service),
            )

        case "rebuild":
            require_service(service)
            if service not in REBUILD_SERVICES:
                raise ValueError(
                    f"rebuild not supported for '{service}'; "
                    f"supported: {', '.join(sorted(REBUILD_SERVICES))}"
                )
            msg = await _rebuild(ctl, service)
            return {"status": "ok", "message": msg}

        case "sync_restart":
            require_service(service)
            if service not in SYNC_RESTART_SERVICES:
                raise ValueError(
                    f"sync_restart not supported for '{service}'; "
                    f"supported: {', '.join(sorted(SYNC_RESTART_SERVICES))}"
                )
            return await run_gated(
                ctl.restart_gate,
                "sync_restart",
                service,
                force=bool(params.get("force", False)),
                lifecycle=lambda: _sync_restart(ctl, service),
            )

        case _:
            raise ValueError(
                f"Unknown method: '{method}'. "
                "Valid: status, health, wait_healthy, start, stop, restart, "
                "sync_restart, rebuild"
            )


def require_service(service: str) -> None:
    """Raise ValueError if service is not a known service name."""
    if service not in VALID_SERVICES:
        raise ValueError(
            f"Unknown service: '{service}'. Valid: {', '.join(sorted(VALID_SERVICES))}"
        )


def write_json(writer: asyncio.StreamWriter, obj: dict[str, Any]) -> None:
    """Serialize obj to JSON and write a newline-terminated line to writer."""
    writer.write(json.dumps(obj).encode() + b"\n")


def _check_one(svc: ServiceState, service: str) -> ServiceInfo:
    """Return ServiceInfo for a single service (synchronous, call via to_thread)."""
    if service not in VALID_SERVICES:
        raise ValueError(f"Unknown service: '{service}'")
    try:
        return getattr(svc, f"check_{service}")()
    except AttributeError:
        raise ValueError(f"Unknown service: '{service}'")


def _gateway_build_status(ctl: ServiceController) -> dict[str, Any]:
    """Return gateway build state for status/progress probes."""
    info = ctl.check_image()
    return {
        "running": ctl.build_running,
        "image_status": info.status.value,
        "image_id": info.image_id,
        "created": info.created,
        "size": info.size,
    }


async def _wait_healthy(svc: ServiceState, service: str, timeout: float) -> float:
    """Poll service health until RUNNING or timeout; return elapsed seconds."""
    from .model.service_state import ServiceStatus

    t0 = time.monotonic()
    deadline = t0 + timeout
    while True:
        info = await asyncio.to_thread(_check_one, svc, service)
        if info.status == ServiceStatus.RUNNING:
            return time.monotonic() - t0
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"'{service}' not healthy after {timeout:.0f}s "
                f"(last status: {info.status.value})"
            )
        await asyncio.sleep(2.0)


async def _start(ctl: ServiceController, service: str) -> str:
    """Call the appropriate ServiceController start method."""
    if service not in VALID_SERVICES:
        raise ValueError(f"Unknown service: '{service}'")
    return await getattr(ctl, f"start_{service}")()


async def _stop(ctl: ServiceController, service: str) -> str:
    """Call the appropriate ServiceController stop method."""
    if service not in VALID_SERVICES:
        raise ValueError(f"Unknown service: '{service}'")
    return await getattr(ctl, f"stop_{service}")()


async def _restart_cycle(ctl: ServiceController, service: str) -> str:
    """Stop then start a service (the 'restart' action), returning a combined message.

    Packaged so `run_gated` holds the restart-mutex slot across the full
    stop→start cycle and releases it once both complete.
    """
    stop_msg = await _stop(ctl, service)
    start_msg = await _start(ctl, service)
    return f"{stop_msg}\n{start_msg}"


async def _rebuild(ctl: ServiceController, service: str) -> str:
    """Full --no-cache rebuild + restart of a managed service.

    Heavy path — pulls fresh base images and rebuilds every layer. For container
    services this can take tens of minutes (gateway: 60-90 min recompiling vLLM
    CUDA kernels). Reserved for engine/pip/Dockerfile changes; agents should
    prefer 'sync_restart' for routine code edits.

    `gateway` is intentionally absent here: agents reach gateway only via
    'sync_restart' (which is just 'restart' under bind-mount). Engine rebuilds
    go through the TUI Build Image flow.
    """
    if service == "mcp":
        return await ctl.rebuild_mcp(no_cache=True)
    if service == "event_service":
        return await ctl.rebuild_event_service()
    if service == "cortex_api":
        return await ctl.rebuild_cortex_api()
    if service == "agent_bus":
        return await ctl.rebuild_agent_bus()
    if service == "grokbuild_worker":
        return await ctl.rebuild_grokbuild_worker()
    if service == "email_bridge":
        return await ctl.rebuild_email_bridge(no_cache=True)
    raise ValueError(f"rebuild not supported for '{service}'")


# Services whose state or render logic can change the boot surface.
# After `sync_restart` on one of these, the boot-render-diff smoke gate runs
# against the last recorded baseline to surface drift at deploy time rather
# than on the next agent's session start. See agent-bus thread 883.
_BOOT_RENDER_DIFF_SERVICES = frozenset({"cortex_api", "mcp"})


async def _sync_restart(ctl: ServiceController, service: str) -> str:
    """Deploy local source edits and bring the service back up.

    Per-service strategy:
      gateway      → restart (libs/, services/, config/ are bind-mounted)
      mcp          → cached --refresh-source rebuild + restart (~20s)
      stargate, rag, cloud_proxy, cortex_api, agent_bus, event_service → restart
    """
    if service == "mcp":
        msg = await ctl.rebuild_mcp(no_cache=False)
    else:
        stop_msg = await _stop(ctl, service)
        start_msg = await _start(ctl, service)
        msg = f"{stop_msg}\n{start_msg}"

    # Skip boot-render-diff when the mcp recreate was deferred to a background
    # task — the new container isn't healthy yet and the diff would query a
    # transitional state. Use the controller's typed flag rather than parsing
    # the message body.
    skip_diff = service == "mcp" and ctl.mcp_rebuild_scheduled
    if service in _BOOT_RENDER_DIFF_SERVICES and not skip_diff:
        diff_msg = await _run_boot_render_diff()
        if diff_msg:
            msg = f"{msg}\n\n{diff_msg}"

    return msg


async def _run_boot_render_diff() -> str:
    """Run `scripts/boot-render-diff` and return its advisory output.

    Fail-soft: never raises, never blocks sync_restart completion. A
    timeout, non-zero exit, or crashed subprocess becomes a brief warning
    line in the restart log — the restart itself is authoritative.
    """
    import pathlib
    import subprocess

    script = (
        pathlib.Path(__file__).resolve().parents[3] / "scripts" / "boot-render-diff"
    )
    if not script.is_file():
        return ""

    def _run() -> str:
        try:
            proc = subprocess.run(
                [str(script)],
                capture_output=True,
                text=True,
                timeout=45.0,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return (
                "[boot-render-diff] timed out after 45s (advisory; restart unaffected)"
            )
        except Exception as exc:  # pragma: no cover — defensive
            return f"[boot-render-diff] failed: {exc} (advisory; restart unaffected)"

        body = (proc.stdout or "").strip()
        if proc.returncode != 0 and proc.stderr:
            body = (body + "\n" + proc.stderr.strip()).strip()
        return body

    return await asyncio.to_thread(_run)
