"""CDP escalation commission — lane probe + Stargate team-dispatch HTTP.

Hop-cadence and Auto handler call ``read_cdp_lane_snapshot`` for admission
gating; the GET return is the fire-time snap that predecessor capture later
reads. ``observed_at`` is stamped here so LOOKUP_FAILED observe can recover
the read clock after commission.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from cdp_ask.client import CdpAskClient
from claude_bundles.hop_cadence_seat_snap import attach_registry_seated_rows
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.queue import AutoJob

logger = get_logger(__name__)

_RELAY_TIMEOUT = 20.0


def _stamp_snap_read(snap: dict[str, Any]) -> dict[str, Any]:
    """Copy ``snap`` and stamp ``observed_at`` at GET-return time if absent.

    Capture runs after commission; the stamp must be the read clock, not the
    capture clock, or LOOKUP_FAILED cannot recover the fire-time membership gap.
    """
    out = dict(snap)
    if not out.get("observed_at"):
        out["observed_at"] = datetime.now(timezone.utc).isoformat()
    return out


def read_cdp_lane_snapshot(*, client: CdpAskClient | None = None) -> dict[str, Any]:
    """Return active-work plus CSE-registry seated rows, stamped at read.

    Non-dict responses stay ``{}`` (falsy) so existing callers that treat an
    empty mapping as a failed probe do not flip. A successful dict always
    carries ``observed_at`` — server value if present, else this call's clock.
    ``seated_rows`` come from the CSE session registry so hop identity can
    see a seated operator with no in-flight project-ask. Admission scalars
    stay execution-store-only.
    """
    http = client or CdpAskClient()
    snap = http._request("GET", "/v1/project-ask/active-work")
    if not isinstance(snap, dict):
        return {}
    return attach_registry_seated_rows(_stamp_snap_read(snap))


def escalation_lane_refusal(
    snap: dict[str, Any],
    *,
    unattended: bool,
    purpose: str | None = "ask",
) -> tuple[bool, str | None]:
    """Return ``(refuse, lane_label)`` for an escalation commission attempt.

    Purpose-aware Option A: advisor/escalation admits may use the reserved slot;
    transitional additive regime applies while ``seat_count`` exceeds the carve line.
    """
    from cdp_ask.lane_admission import escalation_lane_refusal as _purpose_refusal

    return _purpose_refusal(snap, unattended=unattended, purpose=purpose)


async def commission_cdp_escalation(
    job: AutoJob,
    *,
    model: str,
    reasoning_effort: str | None = None,
    stargate_url: str | None = None,
    purpose: str | None = None,
    mission_kind: str | None = None,
    parent_thread: str | None = None,
    prompt_override: str | None = None,
) -> dict[str, Any]:
    """POST one CDP generate leg to Stargate ``/api/v1/team/dispatch``.

    Uses the same async HTTP client pattern as ``services/mcp-server/tools/frontier.py``.

    *prompt_override* carries a body the caller composed for the successor (hop
    orientation) without mutating the queued job.
    """
    body: dict[str, Any] = {
        "op": "generate",
        "model": model,
        "prompt": prompt_override or job.body,
        "dispatch_thread_id": job.thread_id,
        "contract": "light-bounded",
        "caller_agent": "cursor-auto",
    }
    if purpose:
        body["purpose"] = purpose
    if mission_kind:
        body["mission_kind"] = mission_kind
    if parent_thread:
        body["parent_thread"] = parent_thread
    effort = (reasoning_effort or "").strip().lower()
    if effort:
        body["reasoning_effort"] = effort

    endpoint = "/api/v1/team/dispatch"
    base = (stargate_url or DEFAULT_STARGATE_URL).rstrip("/")
    async with make_async_client(base, timeout=_RELAY_TIMEOUT) as client:
        try:
            resp = await client.post(endpoint, json=body)
        except httpx.RequestError as exc:
            logger.error("cdp escalation relay transport failure: %s", exc)
            return {"ok": False, "error": str(exc), "reason": "stargate_unreachable"}

    try:
        payload = resp.json()
    except ValueError:
        return {
            "ok": False,
            "status_code": resp.status_code,
            "error": "non_json_response",
        }

    if resp.status_code >= 400 or (isinstance(payload, dict) and payload.get("error")):
        return {
            "ok": False,
            "status_code": resp.status_code,
            "error": payload,
        }

    execution_id = ""
    if isinstance(payload, dict):
        execution_id = str(payload.get("execution_id") or "")
    logger.info(
        "cdp escalation commissioned job=%s model=%s execution_id=%s",
        job.job_id,
        model,
        execution_id,
    )
    return {
        "ok": True,
        "status_code": resp.status_code,
        "execution_id": execution_id,
        "dispatch": payload,
    }
