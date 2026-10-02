"""Non-hop CDP escalation commission for a cursor-auto job.

Moved from ``cursor_auto/cdp_escalation.py`` after the Auto package was
deleted. A hop commission stays on ``agent_bus.hop`` (fork 15). This body
is only the non-hop generate.
"""

from __future__ import annotations

from typing import Any

import httpx
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_RELAY_TIMEOUT = 20.0


def nonhop_escalation_generate_job(cursor_auto_job: str | None) -> str:
    """Generate ``job`` for a non-hop escalation.

    The cursor-auto id (``answer``, ``ask``, ``verify``, ``execute``,
    ``propagate``, ``seed``, ``recon``, or any other request job) is not
    the generate job.
    """
    del cursor_auto_job
    return "freeform"


async def commission_cdp_escalation(
    *,
    model: str,
    prompt: str,
    thread_id: str,
    cursor_auto_job: str | None = None,
    mission_kind: str | None = None,
    reasoning_effort: str | None = None,
    stargate_url: str | None = None,
    parent_thread: str | None = None,
) -> dict[str, Any]:
    """POST one non-hop CDP generate to Stargate ``/api/v1/team/dispatch``.

    ``mission_kind=hop`` is the hop exemption and is not commissioned here.
    """
    if (mission_kind or "").strip().lower() == "hop":
        return {
            "ok": False,
            "reason": "hop_exemption",
            "error": "hop commissions stay on the hop payload",
        }

    body: dict[str, Any] = {
        "op": "generate",
        "model": model,
        "prompt": prompt,
        "dispatch_thread_id": thread_id,
        "job": nonhop_escalation_generate_job(cursor_auto_job),
        "caller_agent": "cursor-auto",
    }
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
            "json": body,
        }

    if resp.status_code >= 400 or (isinstance(payload, dict) and payload.get("error")):
        return {
            "ok": False,
            "status_code": resp.status_code,
            "error": payload,
            "json": body,
        }

    execution_id = ""
    if isinstance(payload, dict):
        execution_id = str(payload.get("execution_id") or "")
    return {
        "ok": True,
        "status_code": resp.status_code,
        "execution_id": execution_id,
        "json": body,
        "dispatch": payload,
    }
