"""Queue/I2 probes and proof-of-live checks for cursor-auto propagation."""

from __future__ import annotations

import os
from typing import Any

import httpx
from implement_admission.propagation_row import PropagationRow

_GIW_QUEUE_URL = os.environ.get(
    "GIT_INTEGRATION_WORKER_QUEUE_URL",
    "http://127.0.0.1:8091/api/v1/git/cursor-auto/queue",
)
_GIW_LIVENESS_URL = os.environ.get(
    "GIT_INTEGRATION_WORKER_LIVENESS_URL",
    "http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness",
)
_MCP_HEALTH_URL = os.environ.get("MCP_HEALTH_URL", "http://127.0.0.1:8080/health")


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
        return _fetch_json(_MCP_HEALTH_URL)
    return None


def proof_observed(row: PropagationRow, payload: dict[str, Any] | None) -> bool:
    if payload is None:
        return False
    observed = payload.get("code_version")
    return isinstance(observed, str) and observed == row.code_ref


__all__ = [
    "giw_i2_clear",
    "probe_process_live",
    "proof_observed",
    "row_key",
]
