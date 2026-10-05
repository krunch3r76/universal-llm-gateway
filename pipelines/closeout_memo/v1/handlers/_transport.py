"""Followup, harvest, bus turn, and pager. Injected in tests."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_STARGATE_URL,
    make_async_client,
)

logger = logging.getLogger(__name__)

FOLLOWUP_TIMEOUT_S = 120.0


def project_ask_base() -> str:
    return os.environ.get("PROJECT_ASK_URL", "").strip().rstrip("/")


def followup_body(*, parent_thread: str, prompt_text: str) -> dict[str, Any]:
    """Followup JSON. Identity fields are absent on purpose (friction 37834)."""
    return {
        "parent_thread": parent_thread,
        "purpose": "operator-proxy",
        "prompt_text": prompt_text,
        "min_receipt": "dom_committed",
        "reattach": False,
        "timeout_s": 60,
    }


async def post_followup(
    *,
    parent_thread: str,
    prompt_text: str,
) -> dict[str, Any]:
    """POST ``/v1/project-ask/followups``. No CSE identity fields.

    Returns ``{timed_out, status_code, body}``.
    """
    body = followup_body(parent_thread=parent_thread, prompt_text=prompt_text)
    base = project_ask_base()
    if not base:
        return {
            "timed_out": False,
            "status_code": 0,
            "body": {"ok": False, "error": "seat_unavailable"},
        }
    try:
        async with make_async_client(base, timeout=FOLLOWUP_TIMEOUT_S) as client:
            resp = await client.post("/v1/project-ask/followups", json=body)
    except httpx.TimeoutException:
        return {"timed_out": True, "status_code": 0, "body": None}
    except httpx.HTTPError as exc:
        logger.warning("closeout memo followup transport error: %s", exc)
        return {
            "timed_out": False,
            "status_code": 0,
            "body": {"ok": False, "error": "seat_unavailable", "detail": type(exc).__name__},
        }
    try:
        parsed = resp.json()
    except ValueError:
        parsed = {"ok": False, "error": "other"}
    if not isinstance(parsed, dict):
        parsed = {"ok": False, "error": "other"}
    return {"timed_out": False, "status_code": resp.status_code, "body": parsed}


def _seat_for_lane(parent_thread: str) -> tuple[str | None, str | None]:
    """registration_id, chat_url from the landed lane resolver. Never raises."""
    from cdp_ask.lane_current_cse import resolve_lane_current_cse

    body = resolve_lane_current_cse(parent_thread, snap=None)
    current = body.get("current") if isinstance(body, dict) else None
    if not isinstance(current, dict):
        return None, None
    regs = current.get("registration_ids")
    registration_id = None
    if isinstance(regs, list) and regs:
        registration_id = str(regs[0])
    elif current.get("registration_id"):
        registration_id = str(current["registration_id"])
    url = str(current.get("chat_url") or "").strip() or None
    return registration_id, url


async def harvest_marker(
    *,
    marker: str,
    registration_id: str | None,
    parent_thread: str | None = None,
) -> bool | None:
    """True when the lane seat contains ``marker``.

    None when there is no seat to harvest. Callers must fall back instead of
    pasting again: a timeout with no target is how a landed paste gets duplicated.
    """
    chat_url = None
    if not registration_id and parent_thread:
        registration_id, chat_url = _seat_for_lane(parent_thread)
    if not registration_id and not chat_url:
        return None
    base = project_ask_base()
    if not base:
        return False
    payload: dict[str, Any] = {"marker": marker, "reattach": False, "limit": 10}
    if registration_id:
        payload["registration_id"] = registration_id
    elif chat_url:
        payload["chat_url"] = chat_url
    try:
        async with make_async_client(base, timeout=30.0) as client:
            resp = await client.post("/v1/cse-session/harvest", json=payload)
    except httpx.HTTPError:
        return False
    try:
        parsed = resp.json()
    except ValueError:
        return False
    if not isinstance(parsed, dict):
        return False
    if parsed.get("marker_present") is True:
        return True
    blob = str(parsed)
    return marker in blob and parsed.get("outcome") not in {None, "error"}


async def post_bus_turn(
    *,
    wake_lane: str,
    subject: str,
    body: str,
) -> bool:
    """Pointer-only bus turn. False on transport or HTTP failure."""
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = {
        "thread": wake_lane,
        "from": "closeout-memo",
        "to": "web-anthropic",
        "subject": subject[:180],
        "body": body,
        "status": "open",
    }
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
            resp = await client.post("/turns", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("closeout memo bus fallback failed: %s", exc)
        return False
    return resp.status_code < 400


async def page_lane(*, wake_lane: str, subject: str, body: str) -> bool:
    """One pager notify. Coalescing is the ledger's job; this only sends."""
    try:
        from pager_notify.client import notify_pager

        result = await notify_pager(subject, body, tag="closeout-memo")
    except Exception:  # noqa: BLE001
        logger.warning("closeout memo page failed lane=%s", wake_lane, exc_info=True)
        return False
    return bool(result)


def stargate_base() -> str:
    return DEFAULT_STARGATE_URL
