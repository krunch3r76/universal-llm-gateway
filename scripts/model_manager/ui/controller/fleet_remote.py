"""Remote-node helpers for fleet orchestration (free functions, no Textual).

Split out of fleet.py to keep each module ≤300 SLOC. Every function emits
progress through an injected FleetProgressSink and takes the workspace *root*
explicitly — no view state, no app handle. Lifted verbatim from
view/widgets/topology_panel.py with the sink/root substitution recipe.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from universal_logging import get_logger

from scripts.model_manager.observation_event import emit_fleet_relay_status

from .operation_log import tee_with_summary
from .restart_drain import HttpActiveWorkProbe
from .service_config import (
    cdp_ask_url_config,
    is_cdp_ask_manage_enabled,
    resolve_cdp_ask_remote_target,
)
from .topology import (
    deploy_remote,
    gateway_image_mismatch_warnings,
    list_remotes,
    stop_remote,
    wait_for_relay_connected,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence
    from pathlib import Path

    from .fleet import FleetProgressSink

logger = get_logger(__name__)

# Row key used for the master node.  All status updates target this key; if the
# master's stargate_id ever becomes user-configurable this constant must follow.
_MASTER_ROW_KEY = "localhost"


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
            "mcp server synced",
            "mcp rebuild scheduled",
            "cortex api started",
            "cortex api stopped",
            "agent bus started",
            "agent bus stopped",
            "cdp-ask",
            "sidecar",
        )
    ):
        return True
    if any(
        k in lower
        for k in (
            "gateway container started",
            "starting (pid",
            "is not running",
            "is already running",
            "stopped (pid",
            "already exited",
            "sigkill'd after",
            "rebuild_scheduled",
            "skipped",
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


async def stop_remote_before_operation(
    *,
    hostname: str,
    address: str,
    operation: str,
    results: dict[str, bool],
    sink: FleetProgressSink,
) -> None:
    """Stop a remote relay+edge before fleet operations."""
    raw: AsyncIterator[str] = stop_remote(hostname=hostname, address=address)
    summary = tee_with_summary(raw, operation="deploy", host=hostname)
    failed = False
    try:
        async for line in summary:
            sink.line(hostname, line)
            if "[red]" in line:
                failed = True
    except Exception as e:
        sink.line(hostname, f"Error stopping remote before {operation}: {e}")
        logger.exception("Error stopping remote %s before %s", hostname, operation)
        failed = True

    if failed:
        sink.status(hostname, "✗ stop failed")
        sink.line(hostname, f"--- {hostname}: ✗ stop failed ---")
        results[hostname] = False
        return

    sink.status(hostname, "○ stopped, next phase pending")
    sink.line(hostname, f"--- {hostname}: ✓ stopped ---")
    results[hostname] = True


async def deploy_and_build_remote(
    *,
    hostname: str,
    address: str,
    scope: str,
    results: dict[str, bool],
    sink: FleetProgressSink,
    root: Path,
) -> None:
    """Deploy and build on a remote node (connection verification deferred)."""
    raw: AsyncIterator[str] = deploy_remote(
        hostname=hostname,
        address=address,
        workspace_root=root,
        build=True,
        restart=True,
        scope=scope,
    )
    summary = tee_with_summary(raw, operation="deploy", host=hostname)
    failed = False
    try:
        async for line in summary:
            sink.line(hostname, line)
            if "[red]" in line:
                failed = True
    except Exception as e:
        sink.line(hostname, f"Error during remote deploy: {e}")
        logger.exception("Error deploying remote %s", hostname)
        failed = True

    if failed:
        sink.status(hostname, "✗ failed")
        sink.line(hostname, f"--- {hostname}: ✗ failed ---")
        results[hostname] = False
    else:
        sink.status(hostname, "✓ built")
        results[hostname] = True
        for wline in await gateway_image_mismatch_warnings(
            hostname=hostname, address=address
        ):
            sink.line(hostname, wline)


async def verify_relay_connection(hostname: str, *, sink: FleetProgressSink) -> bool:
    """Verify a relay registered with master after deploy."""
    remote_id = f"relay-{hostname}"
    sink.status(hostname, "⟳ connecting...")
    sink.line(hostname, f"[{hostname}] Waiting for relay to register with master...")
    t0 = time.monotonic()
    result = await wait_for_relay_connected(remote_id)
    elapsed = time.monotonic() - t0
    status = "● connected" if result.connected else "◌ unreachable"
    sink.status(hostname, status)
    await emit_fleet_relay_status(
        hostname=hostname, connected=result.connected, duration_s=elapsed
    )
    if not result.connected and result.reason:
        sink.line(hostname, f"  reason: {result.reason}")
    if not result.connected:
        sink.line(hostname, "  relay did not register in time")
        sink.line(
            hostname,
            "  check: SSH_USER, ~/.gateway/nodes/<host>.env, FEDERATION_KEY_RELAY",
        )
        sink.line(
            hostname,
            "  check: remote ./manage relay --restart output for auth/connection errors",
        )
    sink.line(hostname, f"--- {hostname}: {status} ---")
    return result.connected


async def deploy_remotes_parallel(
    *, build: bool, scope: str, sink: FleetProgressSink, root: Path
) -> dict[str, bool]:
    """Deploy all remotes in parallel via TaskGroup."""
    remotes = list_remotes()
    if not remotes:
        sink.line(_MASTER_ROW_KEY, "No remotes configured.")
        return {}

    targets = _parse_remote_targets(remotes)
    results: dict[str, bool] = {}
    first = True
    async with asyncio.TaskGroup() as tg:
        tasks: list[asyncio.Task[tuple[str, bool]]] = []
        for hostname, address in targets:
            sink.status(hostname, "⟳ deploying...")
            if first:
                sink.focus(hostname)
                first = False
            tasks.append(
                tg.create_task(
                    deploy_single_remote(
                        hostname=hostname,
                        address=address,
                        build=build,
                        scope=scope,
                        sink=sink,
                        root=root,
                    )
                )
            )
    for task in tasks:
        hostname, ok = task.result()
        results[hostname] = ok
    return results


async def deploy_single_remote(
    *,
    hostname: str,
    address: str,
    build: bool,
    scope: str,
    sink: FleetProgressSink,
    root: Path,
) -> tuple[str, bool]:
    """Deploy one remote, buffering output to its node key."""
    raw: AsyncIterator[str] = deploy_remote(
        hostname=hostname,
        address=address,
        workspace_root=root,
        build=build,
        restart=True,
        scope=scope,
    )
    summary = tee_with_summary(raw, operation="deploy", host=hostname)
    failed = False
    try:
        async for line in summary:
            sink.line(hostname, line)
            if "[red]" in line:
                failed = True
    except Exception as e:
        sink.line(hostname, f"Error during remote deploy: {e}")
        logger.exception("Error deploying remote %s", hostname)
        failed = True

    if failed:
        sink.status(hostname, "✗ failed")
        sink.line(hostname, f"--- {hostname}: ✗ failed ---")
        return hostname, False

    if build:
        for wline in await gateway_image_mismatch_warnings(
            hostname=hostname, address=address
        ):
            sink.line(hostname, wline)

    connected = await verify_relay_connection(hostname, sink=sink)
    if not connected:
        return hostname, False
    await _maybe_restart_remote_cdp_ask(hostname, root=root, sink=sink)
    return hostname, True


async def _maybe_restart_remote_cdp_ask(
    hostname: str, *, root: Path, sink: FleetProgressSink
) -> None:
    """When cdp-ask is enabled for this remote, sync+restart after fleet deploy."""
    if not is_cdp_ask_manage_enabled():
        return
    cfg = cdp_ask_url_config()
    if cfg is None:
        return
    url_host, _port, _base = cfg
    resolved = resolve_cdp_ask_remote_target(url_host)
    if resolved is None:
        return
    remote_hostname, _address, _ssh_user = resolved
    if remote_hostname != hostname:
        return
    _host, _port, base = cfg
    try:
        work = await HttpActiveWorkProbe(
            base, "/v1/project-ask/drain-state"
        ).snapshot()
    except Exception as exc:
        sink.line(
            hostname,
            f"  ⚠ cdp_ask sync_restart deferred (drain-state probe failed: {exc})",
        )
        return
    if work.busy:
        running_count = work.detail.get("running_count", 0)
        sink.line(
            hostname,
            "  ⚠ cdp_ask sync_restart deferred "
            f"({running_count} recorded execution(s))",
        )
        ids = work.detail.get("execution_ids") or []
        if ids:
            preview = ", ".join(ids[:3])
            suffix = "…" if len(ids) > 3 else ""
            sink.line(hostname, f"    execution_ids: {preview}{suffix}")
        return
    from .service_ctl.cdp_ask_remote import sync_restart_cdp_ask_remote

    try:
        msg = await sync_restart_cdp_ask_remote(root)
        ok = _classify_result(msg)
        sink.line(hostname, f"  {'✓' if ok else '✗'} cdp_ask sync_restart")
        if not ok:
            sink.line(hostname, f"    {msg.splitlines()[0]}")
    except Exception as exc:
        logger.exception("cdp_ask fleet restart failed for %s", hostname)
        sink.line(hostname, f"  ✗ cdp_ask sync_restart: {exc}")
