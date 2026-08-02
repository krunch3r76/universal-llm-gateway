"""Queue/I2 probes and proof-of-live checks for cursor-auto propagation."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from deploy_identity.code_ref_relation import code_ref_satisfied
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
    """Probe closure surface for one propagation row via the B1 registry."""
    from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
        dispatch_proof_probe,
    )

    result = dispatch_proof_probe(row)
    if result.error is not None:
        return None
    return result.payload


def _code_version(payload: dict[str, Any]) -> str | None:
    observed = payload.get("code_version")
    return observed if isinstance(observed, str) else None


def process_identity(payload: dict[str, Any]) -> str | None:
    """Return a comparable process-identity key from a health/liveness payload."""
    pid = payload.get("pid")
    if pid is not None:
        return f"pid:{pid}"
    start = payload.get("process_start_time")
    if isinstance(start, str) and start.strip():
        return f"start:{start.strip()}"
    age = payload.get("process_age_s")
    if isinstance(age, (int, float)):
        return f"age:{float(age):.6f}"
    uptime = payload.get("uptime_s")
    if isinstance(uptime, (int, float)):
        return f"uptime:{float(uptime):.6f}"
    return None


def strong_process_identity(payload: dict[str, Any]) -> bool:
    """True when identity binds a specific OS process, not uptime alone."""
    if payload.get("pid") is not None:
        return True
    start = payload.get("process_start_time")
    if isinstance(start, str) and start.strip():
        return True
    return isinstance(payload.get("process_age_s"), (int, float))


def _process_live_identity_delta(
    before: dict[str, Any],
    after: dict[str, Any],
) -> bool:
    before_id = process_identity(before)
    after_id = process_identity(after)
    if before_id is None or after_id is None:
        return False
    return before_id != after_id


def _probe_is_outgoing_generation(
    payload: dict[str, Any],
    *,
    settle_not_before_monotonic: float,
) -> bool:
    """True when uptime_s implies the probed process predates restart completion."""
    uptime = payload.get("uptime_s")
    if not isinstance(uptime, (int, float)):
        return False
    process_started_monotonic = time.monotonic() - float(uptime)
    return process_started_monotonic < settle_not_before_monotonic


def _section_code_ref_satisfied(section: dict[str, Any], code_ref: str) -> bool:
    version = section.get("code_version")
    return isinstance(version, str) and code_ref_satisfied(code_ref, version)


def proof_observed(
    row: PropagationRow,
    payload: dict[str, Any] | None,
    *,
    before: dict[str, Any] | None = None,
    settle_not_before_monotonic: float | None = None,
    probed_surface: str | None = None,
) -> bool:
    from services.git_integration_worker.cursor_sdk_boundary_deployment_identity import (
        DeploymentIdentityEmit,
        emit_deployment_identity_boundary,
    )

    surface = probed_surface or row.service
    emit_deployment_identity_boundary(
        DeploymentIdentityEmit(
            expected_executor=row.service,
            probed_surface=surface,
            payload=payload,
            code_ref=row.code_ref,
            before_payload=before,
            landed_at_monotonic=settle_not_before_monotonic,
        )
    )
    if payload is None:
        return False
    if row.proof_class == "served_artifact":
        from services.git_integration_worker.cursor_auto.propagation_served_artifact import (
            served_artifact_descriptor,
            served_artifact_observed,
        )

        descriptor = served_artifact_descriptor(row.service)
        if descriptor is None:
            return False
        expected = row.expected_x_mcp_count or descriptor.expected_x_mcp_count
        return served_artifact_observed(
            payload,
            code_ref=row.code_ref,
            expected_x_mcp_count=expected,
        )
    if row.proof_class == "client_visible" and row.service == "mcp":
        mcp_health = payload.get("mcp_health")
        cortex_health = payload.get("cortex_api")
        if not isinstance(mcp_health, dict) or not isinstance(cortex_health, dict):
            return False
        return _section_code_ref_satisfied(
            mcp_health, row.code_ref
        ) and _section_code_ref_satisfied(cortex_health, row.code_ref)
    observed = _code_version(payload)
    if not isinstance(observed, str) or not code_ref_satisfied(row.code_ref, observed):
        return False
    if before is not None:
        return _process_live_identity_delta(before, payload)
    if settle_not_before_monotonic is not None:
        if _probe_is_outgoing_generation(
            payload, settle_not_before_monotonic=settle_not_before_monotonic
        ):
            return False
        return strong_process_identity(payload)
    return False


__all__ = [
    "giw_i2_clear",
    "probe_for_row",
    "probe_process_live",
    "process_identity",
    "proof_observed",
    "row_key",
    "strong_process_identity",
]
