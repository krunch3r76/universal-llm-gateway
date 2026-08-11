"""Structured process-identity observations from manage stop/start lifecycle.

Manage already observes host PIDs and container StartedAt during restart; this
module captures those values as structured authority identity for propagation
harvest proof closure (Option C — authority-primary with readiness join).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Literal, TypedDict

from ..service_config import GATEWAY_DIR
from ...model.service_state import ServiceState

logger = logging.getLogger(__name__)

IdentitySource = Literal["manage_host_pid", "manage_container_started_at"]


class AuthorityIdentity(TypedDict, total=False):
    """Process identity observed by manage during a sync_restart cycle."""

    service: str
    old: str | int | None
    new: str | int | None
    identity_source: IdentitySource
    old_identity_source: IdentitySource
    new_identity_source: IdentitySource
    readiness_proven: bool
    intent_id: str | None


class AuthorityIdentitySnapshot(TypedDict):
    """Pre-restart identity capture returned by ``snapshot_before_restart``."""

    old: str | int | None
    identity_source: IdentitySource


_HOST_PID_FILES: dict[str, Path] = {
    "stargate": GATEWAY_DIR / "stargate.pid",
    "rag": GATEWAY_DIR / "rag.pid",
    "cloud_proxy": GATEWAY_DIR / "cloud-proxy.pid",
    "cortex_api": GATEWAY_DIR / "cortex-api.pid",
    "agent_bus": GATEWAY_DIR / "agent-bus.pid",
    "git_integration_worker": GATEWAY_DIR / "git-integration-worker.pid",
    "event_service": GATEWAY_DIR / "event-service.pid",
    "cdp_ask": GATEWAY_DIR / "cdp-ask.pid",
}

_CONTAINER_NAMES: dict[str, str] = {
    "mcp": "mcp-server",
    "gateway": os.environ.get("GATEWAY_CONTAINER", "edge-localhost"),
}


def _read_host_pid(service_state: ServiceState, pid_file: Path) -> int | None:
    """Return the live PID from a manage PID file, or None when absent or stale."""
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None
    if service_state._pid_alive(pid):
        return pid
    return None


async def _read_container_started_at(container_name: str) -> str | None:
    """Return docker ``State.StartedAt`` for a running container, or None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            container_name,
            "--format",
            "{{.State.StartedAt}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        logger.debug("container inspect spawn failed for %s: %s", container_name, exc)
        return None
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    text = out.decode(errors="replace").strip()
    if not text or text.startswith("0001-01-01"):
        return None
    return text


async def _read_current_identity(
    service_state: ServiceState,
    service: str,
) -> tuple[str | int | None, IdentitySource | None]:
    """Read the current identity value and source for one managed service."""
    if service in _HOST_PID_FILES:
        pid = _read_host_pid(service_state, _HOST_PID_FILES[service])
        return pid, "manage_host_pid"
    if service in _CONTAINER_NAMES:
        started_at = await _read_container_started_at(_CONTAINER_NAMES[service])
        return started_at, "manage_container_started_at"
    return None, None


async def snapshot_before_restart(
    service_state: ServiceState,
    service: str,
) -> AuthorityIdentitySnapshot | None:
    """Capture pre-restart authority identity before stop/start runs.

    Returns None when the service has no configured identity oracle (e.g.
    unsupported slug). Callers merge the snapshot with post-restart values via
    ``build_authority_identity``.
    """
    old, source = await _read_current_identity(service_state, service)
    if source is None:
        return None
    return AuthorityIdentitySnapshot(old=old, identity_source=source)


async def read_after_restart_identity(
    service_state: ServiceState,
    service: str,
) -> tuple[str | int | None, IdentitySource | None]:
    """Read post-restart identity for harvest finalize after wait_healthy."""
    return await _read_current_identity(service_state, service)


def build_authority_identity(
    service: str,
    *,
    old: str | int | None,
    new: str | int | None,
    identity_source: IdentitySource,
    old_identity_source: IdentitySource | None = None,
    new_identity_source: IdentitySource | None = None,
    readiness_proven: bool = False,
    intent_id: str | None = None,
) -> AuthorityIdentity:
    """Assemble the authority identity record threaded through harvest proof closure."""
    record: AuthorityIdentity = {
        "service": service,
        "old": old,
        "new": new,
        "identity_source": identity_source,
        "readiness_proven": readiness_proven,
        "intent_id": intent_id,
    }
    if old_identity_source is not None:
        record["old_identity_source"] = old_identity_source
    if new_identity_source is not None:
        record["new_identity_source"] = new_identity_source
    return record


async def finalize_authority_identity(
    service_state: ServiceState,
    service: str,
    before: AuthorityIdentitySnapshot | None,
    *,
    readiness_proven: bool,
    intent_id: str | None = None,
) -> AuthorityIdentity | None:
    """Build authority identity after restart using a captured pre-restart snapshot."""
    if before is None:
        return None
    new_value, new_source = await read_after_restart_identity(service_state, service)
    source = before["identity_source"]
    return build_authority_identity(
        service,
        old=before["old"],
        new=new_value,
        identity_source=source,
        old_identity_source=before["identity_source"],
        new_identity_source=new_source or source,
        readiness_proven=readiness_proven,
        intent_id=intent_id,
    )


def normalize_authority_value(value: str | int | None) -> str | None:
    """Normalize authority old/new values for equality comparison."""
    if value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = [
    "AuthorityIdentity",
    "AuthorityIdentitySnapshot",
    "IdentitySource",
    "build_authority_identity",
    "finalize_authority_identity",
    "normalize_authority_value",
    "read_after_restart_identity",
    "snapshot_before_restart",
]
