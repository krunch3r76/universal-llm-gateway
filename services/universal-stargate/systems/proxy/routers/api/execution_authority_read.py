"""Stargate client for satellite execution authority reads."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from cdp_ask.client import project_ask_base_url, relay_async
from universal_logging import get_logger

from systems.frontier_consult.cdp_generate_inflight_ledger import read_inflight_leg

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AuthorityReadResult:
    ok: bool
    payload: dict[str, Any] | None
    reason: str | None = None
    degraded: bool = False


async def read_execution_authority(
    execution_id: str,
) -> AuthorityReadResult:
    """Fetch authority + store from the cdp_ask satellite."""
    base = project_ask_base_url()
    if not base:
        return AuthorityReadResult(
            ok=False,
            payload=None,
            reason="project_ask_url_unset",
            degraded=True,
        )
    leg = await asyncio.to_thread(read_inflight_leg, execution_id)
    satellite_id = leg.satellite_execution_id if leg is not None else None
    path = f"/v1/project-ask/executions/{execution_id}/state"
    if satellite_id:
        path = f"{path}?satellite_execution_id={satellite_id}"
    try:
        status, body, _media = await relay_async("GET", path)
    except Exception as exc:  # pragma: no cover - transport
        logger.warning(
            "authority read transport error execution_id=%s: %s",
            execution_id,
            exc,
        )
        return AuthorityReadResult(
            ok=False,
            payload=None,
            reason=f"transport_error:{exc}",
            degraded=True,
        )
    if status >= 400 or not isinstance(body, dict):
        return AuthorityReadResult(
            ok=False,
            payload=None,
            reason=f"http_{status}",
            degraded=True,
        )
    return AuthorityReadResult(ok=True, payload=body)


def map_authority_to_monitor(
    authority: dict[str, Any],
    *,
    execution_id: str,
) -> dict[str, Any] | None:
    """Map satellite authority payload to monitor fields, or None when absent."""
    entry = authority.get("execution_state")
    if not isinstance(entry, dict):
        store = authority.get("store")
        if isinstance(store, dict):
            return _map_store_projection(
                store, authority=authority, execution_id=execution_id
            )
        return None
    freshness = authority.get("execution_state_freshness")
    state = str(entry.get("state") or "")
    if freshness == "expired_by_ttl" and state in {"seated", "streaming"}:
        state = "expired"
    status, error = _monitor_status_for_state(state)
    from datetime import UTC, datetime

    from universal_protocol.status_basis import (
        SOURCE_CDP_REGISTRY_EXECUTION_STATE,
        status_basis,
    )

    updated = entry.get("updated_at")
    as_of = (
        datetime.fromtimestamp(float(updated), tz=UTC)
        .isoformat()
        .replace("+00:00", "Z")
        if isinstance(updated, (int, float))
        else str(authority.get("as_of") or "")
    )
    payload = status_basis(
        "status",
        status,
        as_of=as_of,
        source=SOURCE_CDP_REGISTRY_EXECUTION_STATE,
        scope=f"execution:{execution_id}",
        epoch={"holder_pid": entry.get("holder_pid"), "stargate_pid": os.getpid()},
        recovery={"consulted": ["cdp_registry.execution_state"]},
        state=state,
        execution_id=execution_id,
    )
    if error is not None:
        payload["error"] = error
    if freshness == "expired_by_ttl":
        payload["execution_state_freshness"] = freshness
    return payload


def _map_store_projection(
    store: dict[str, Any],
    *,
    authority: dict[str, Any],
    execution_id: str,
) -> dict[str, Any]:
    from universal_protocol.status_basis import (
        SOURCE_CDP_ASK_EXECUTION_STORE,
        status_basis,
    )

    status = str(store.get("status") or "running")
    return status_basis(
        "status",
        status,
        as_of=str(authority.get("as_of") or ""),
        source=SOURCE_CDP_ASK_EXECUTION_STORE,
        scope=f"execution:{execution_id}",
        epoch={"stargate_pid": os.getpid()},
        recovery={
            "consulted": ["cdp_registry.execution_state", "cdp_ask.execution_store"],
            "projection_of": "cdp_registry.execution_state",
        },
        execution_id=execution_id,
        projection_of="cdp_registry.execution_state",
    )


def _monitor_status_for_state(
    state: str,
) -> tuple[str, dict[str, Any] | None]:
    running = {"seated", "streaming", "awaiting_wake", "transferred"}
    if state in running:
        return "running", None
    if state == "finished":
        return "completed", None
    if state in {"failed", "aborted"}:
        return "failed", None
    if state == "expired":
        return "failed", {
            "code": "execution_state_expired",
            "message": "Registry execution_state expired",
        }
    return "failed", {"code": "execution_state_unknown", "message": f"state={state}"}
