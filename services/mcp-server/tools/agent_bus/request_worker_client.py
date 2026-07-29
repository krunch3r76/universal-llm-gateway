"""HTTP client for the Cursor Auto worker — liveness probe + admit enqueue.

Split out of ``request.py`` (modularization: the module carried both the
request-shaping logic and its transport). Nothing here knows about bus turns;
callers own the turn write and the response shape.

Sender wire discipline (harvest-restart-propagation I3): new enqueue JSON fields
must be optional-with-default only — never rename or remove existing keys.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

_DEFAULT_WORKER_URL = "http://127.0.0.1:8091"
_AUTO_API_PREFIX = "/api/v1/git/cursor-auto"


def _worker_base_url() -> str:
    """Resolve Auto worker base URL for mcp→host reachability.

    Prefer ``GIT_INTEGRATION_WORKER_URL`` when set. Otherwise, from the mcp
    container, use ``STARGATE_URL`` so enqueue/liveness ride the existing
    ``/api/v1/git/*`` host-side proxy (worker binds 127.0.0.1 — unreachable
    via host.docker.internal). Host-local callers fall back to loopback.
    """
    explicit = os.environ.get("GIT_INTEGRATION_WORKER_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    stargate = os.environ.get("STARGATE_URL", "").strip()
    if stargate:
        return stargate.rstrip("/")
    return _DEFAULT_WORKER_URL


def _auto_url(path: str, *, base_url: str | None = None) -> str:
    base = (base_url or _worker_base_url()).rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{base}{_AUTO_API_PREFIX}{suffix}"


def probe_auto_liveness(
    *,
    base_url: str | None = None,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """Probe git_integration_worker Auto liveness (arm predicate).

    Returns ``{live: bool, ...}``. Transport failure ⇒ ``live=False`` (F1:
    never claim armed without a live handler).
    """
    url = _auto_url("/liveness", base_url=base_url)
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return {
                "live": False,
                "reason": "liveness_http_error",
                "status_code": resp.status_code,
            }
        data = resp.json()
        return {
            "live": bool(data.get("live")),
            "liveness": data,
            "reason": "ok" if data.get("live") else "no_live_handler",
        }
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return {"live": False, "reason": "liveness_unreachable", "error": str(exc)}


def enqueue_auto_job(
    *,
    thread_id: str,
    turn_number: int,
    subject: str,
    body: str,
    from_agent: str,
    to_agent: str,
    desired_model: str,
    desired_effort: str,
    contract: str,
    require_attended: bool = False,
    request_id: str | None = None,
    base_url: str | None = None,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """POST admit-on-request enqueue to the Auto worker."""
    url = _auto_url("/enqueue", base_url=base_url)
    payload = {
        "thread_id": str(thread_id),
        "turn_number": int(turn_number),
        "subject": subject,
        "body": body,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "desired_model": desired_model,
        "desired_effort": desired_effort,
        "contract": contract,
        "require_attended": bool(require_attended),
    }
    if request_id:
        payload["request_id"] = request_id
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, json=payload)
        data = resp.json() if resp.content else {}
        if resp.status_code == 200 and data.get("ok"):
            return {
                "ok": True,
                "handler_status": "auto-admit-armed",
                "enqueue": data,
            }
        return {
            "ok": False,
            "handler_status": data.get("handler_status", "no-auto-handler"),
            "enqueue": data,
            "status_code": resp.status_code,
        }
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return {
            "ok": False,
            "handler_status": "no-auto-handler",
            "reason": "enqueue_unreachable",
            "error": str(exc),
        }


__all__ = [
    "_auto_url",
    "_worker_base_url",
    "enqueue_auto_job",
    "probe_auto_liveness",
]
