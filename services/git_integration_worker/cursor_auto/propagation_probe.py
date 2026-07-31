"""Queue/I2 probes and proof-of-live checks for cursor-auto propagation."""

from __future__ import annotations

import os
from typing import Any

import httpx
from deploy_identity.mcp_health_probe_url import resolve_mcp_health_probe_url
from implement_admission.propagation_row import PropagationRow
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

_GIW_QUEUE_URL = os.environ.get(
    "GIT_INTEGRATION_WORKER_QUEUE_URL",
    "http://127.0.0.1:8091/api/v1/git/cursor-auto/queue",
)
_GIW_LIVENESS_URL = os.environ.get(
    "GIT_INTEGRATION_WORKER_LIVENESS_URL",
    "http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness",
)


def row_key(row: PropagationRow) -> str:
    return f"{row.service}:{row.code_ref}:{row.action}"


def _fetch_json(url: str, *, timeout_s: float = 3.0) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except (httpx.HTTPError, ValueError, OSError):
        return None


def _fetch_cortex_api_health(*, timeout_s: float = 3.0) -> dict[str, Any] | None:
    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=timeout_s) as client:
            resp = client.get("/health")
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except (httpx.HTTPError, ValueError, OSError):
        return None


def giw_i2_clear(*, queue_snapshot: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Return whether GIW restart is permitted under I2 (no in-flight closeout relay)."""
    snapshot = queue_snapshot if queue_snapshot is not None else _fetch_json(_GIW_QUEUE_URL)
    if snapshot is None:
        return False, "i2_queue_unreachable"
    claimed = int(snapshot.get("claimed") or 0)
    pending = int(snapshot.get("pending") or 0)
    if claimed > 0:
        return False, "i2_inflight_closeout"
    if pending > 0:
        return False, "i2_pending_closeout"
    return True, "ok"


def probe_process_live(service: str) -> dict[str, Any] | None:
    """Fetch health/liveness JSON for proof-of-live closure."""
    if service == "git_integration_worker":
        return _fetch_json(_GIW_LIVENESS_URL)
    if service == "mcp":
        return _fetch_json(resolve_mcp_health_probe_url())
    if service == "cortex_api":
        return _fetch_cortex_api_health()
    return None


def probe_for_row(row: PropagationRow) -> dict[str, Any] | None:
    """Probe closure surface for one propagation row (proof_class-aware)."""
    if row.proof_class == "client_visible" and row.service == "mcp":
        mcp_health = _fetch_json(resolve_mcp_health_probe_url())
        cortex_health = _fetch_cortex_api_health()
        if mcp_health is None and cortex_health is None:
            return None
        return {"mcp_health": mcp_health, "cortex_api": cortex_health}
    return probe_process_live(row.service)


def _code_version(payload: dict[str, Any]) -> str | None:
    observed = payload.get("code_version")
    return observed if isinstance(observed, str) else None


def proof_observed(row: PropagationRow, payload: dict[str, Any] | None) -> bool:
    if payload is None:
        return False
    if row.proof_class == "client_visible" and row.service == "mcp":
        mcp_health = payload.get("mcp_health")
        cortex_health = payload.get("cortex_api")
        if not isinstance(mcp_health, dict) or not isinstance(cortex_health, dict):
            return False
        ref = row.code_ref
        return (
            _code_version(mcp_health) == ref
            and _code_version(cortex_health) == ref
        )
    observed = _code_version(payload)
    return isinstance(observed, str) and observed == row.code_ref


__all__ = [
    "giw_i2_clear",
    "probe_for_row",
    "probe_process_live",
    "proof_observed",
    "row_key",
]
