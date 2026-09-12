"""POST ``/api/v1/team/dispatch`` — never GIW-direct."""

from __future__ import annotations

import os
from typing import Any

import httpx

_DEFAULT_STARGATE = os.environ.get("STARGATE_URL", "http://localhost:9999").rstrip("/")
_DISPATCH_PATH = "/api/v1/team/dispatch"
_GIW_MARKERS = (":8091", "/api/v1/cursor/dispatch")
_ALLOWED_FIELDS = frozenset(
    {
        "op",
        "seat",
        "contract",
        "lane",
        "model",
        "packet_path",
        "prompt",
        "message",
        "dispatch_thread_id",
        "work_key",
        "timeout_seconds",
        "tags",
        "prompt",
        "source_ref",
        "caller_agent",
        "cost_intent",
        "cost_intent_reason",
        "force",
        "force_reason",
        "role",
        "system",
    }
)


def _refuse_giw_direct(base_url: str) -> str | None:
    lowered = base_url.lower()
    if any(marker in lowered for marker in _GIW_MARKERS):
        return "refuse_giw_direct: use Stargate /api/v1/team/dispatch, not GIW"
    return None


def submit_team_dispatch(
    body: dict[str, Any],
    *,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> tuple[dict[str, Any], int]:
    """POST team dispatch; return ``(payload, status_code)``."""
    url = (base_url or _DEFAULT_STARGATE).rstrip("/")
    if err := _refuse_giw_direct(url):
        return {"error": {"code": "invalid_base_url", "message": err}}, 400
    filtered = {k: v for k, v in body.items() if k in _ALLOWED_FIELDS and v is not None}
    endpoint = f"{url}{_DISPATCH_PATH}"
    try:
        resp = httpx.post(endpoint, json=filtered, timeout=timeout)
    except httpx.HTTPError as exc:
        return {"error": {"code": "transport_error", "message": str(exc)}}, 503
    try:
        payload = resp.json()
    except ValueError:
        payload = {
            "error": {"code": f"http_{resp.status_code}", "message": resp.text[:500]}
        }
    if not isinstance(payload, dict):
        payload = {"error": {"code": "invalid_response", "message": str(payload)}}
    return payload, resp.status_code
