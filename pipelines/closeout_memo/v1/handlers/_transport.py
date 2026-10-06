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
            "body": {
                "ok": False,
                "error": "seat_unavailable",
                "detail": type(exc).__name__,
            },
        }
    try:
        parsed = resp.json()
    except ValueError:
        parsed = {"ok": False, "error": "other"}
    if not isinstance(parsed, dict):
        parsed = {"ok": False, "error": "other"}
    return {"timed_out": False, "status_code": resp.status_code, "body": parsed}


async def get_lane_attended(*, parent_thread: str) -> dict[str, Any]:
    """GET ``/v1/project-ask/attended-operator`` for a lane.

    Returns ``{status_code, body}``. JSON is parsed on 200, 404, 409, and 503.
    Transport failure or non-JSON yields a body that makes address retry ineligible.
    """
    base = project_ask_base()
    if not base:
        return {
            "status_code": 0,
            "body": {"ok": False, "error": "seat_unavailable"},
        }
    try:
        async with make_async_client(base, timeout=30.0) as client:
            resp = await client.get(
                "/v1/project-ask/attended-operator",
                params={"parent_thread": parent_thread},
            )
    except httpx.TimeoutException:
        return {"status_code": 0, "body": None}
    except httpx.HTTPError as exc:
        logger.warning("closeout memo attended-operator transport error: %s", exc)
        return {
            "status_code": 0,
            "body": {
                "ok": False,
                "error": "seat_unavailable",
                "detail": type(exc).__name__,
            },
        }
    try:
        parsed = resp.json()
    except ValueError:
        parsed = {"ok": False, "error": "other"}
    if not isinstance(parsed, dict):
        parsed = {"ok": False, "error": "other"}
    return {"status_code": resp.status_code, "body": parsed}


def followup_by_address_body(
    *,
    parent_thread: str,
    prompt_text: str,
    chat_url: str,
    registration_id: str | None = None,
) -> dict[str, Any]:
    """Followup JSON with stored ``chat_url`` and ``reattach`` for a parked lane."""
    body = followup_body(parent_thread=parent_thread, prompt_text=prompt_text)
    out: dict[str, Any] = {**body, "chat_url": chat_url, "reattach": True}
    reg = (registration_id or "").strip()
    if reg:
        out["registration_id"] = reg
    return out


async def post_followup_by_address(
    *,
    parent_thread: str,
    prompt_text: str,
    chat_url: str,
    registration_id: str | None = None,
) -> dict[str, Any]:
    """POST followups with ``chat_url`` and ``reattach: true``.

    Returns ``{timed_out, status_code, body}`` like ``post_followup``.
    """
    body = followup_by_address_body(
        parent_thread=parent_thread,
        prompt_text=prompt_text,
        chat_url=chat_url,
        registration_id=registration_id,
    )
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
        logger.warning("closeout memo followup-by-address transport error: %s", exc)
        return {
            "timed_out": False,
            "status_code": 0,
            "body": {
                "ok": False,
                "error": "seat_unavailable",
                "detail": type(exc).__name__,
            },
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
    chat_url: str | None = None,
) -> bool | None:
    """True when the lane seat contains ``marker``.

    None when there is no seat to harvest. Callers must fall back instead of
    pasting again: a timeout with no target is how a landed paste gets duplicated.

    When ``chat_url`` is given and ``registration_id`` is absent, harvest targets
    that address instead of resolving the lane in-process.
    """
    if not registration_id and not chat_url and parent_thread:
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
    """Retired: undelivered closeout memos must not page the phone.

    Kept as a no-op so any stray caller stays silent. Bus fallback is the
    interagent path; Maestro tab tracking owns delivery reliability.
    """
    del subject, body  # signature retained for call-site compatibility
    logger.info("closeout memo page skipped (retired) lane=%s", wake_lane)
    return False


def stargate_base() -> str:
    return DEFAULT_STARGATE_URL
