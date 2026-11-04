"""Relay Grok operator paste to the hub when local Grok CDP is unreachable on this host."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.error import URLError

import httpx
from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.wake_delivery import normalize_target

from cdp_ask.display_chat_models import (
    DisplayChatPasteRequest,
    DisplayChatPasteResponse,
)

logger = logging.getLogger(__name__)


def operator_actuation_paste_url() -> str | None:
    """Hub URL for ``POST /v1/display-chat/paste`` when Grok CDP is not local."""
    env = os.environ.get("GROK_OPERATOR_ACTUATION_URL", "").strip()
    if env:
        return env.rstrip("/")
    try:
        from cursor_bridge.grok_window_bindings import load_config

        cfg = load_config()
        act = cfg.get("operator_actuation")
        if isinstance(act, dict):
            url = str(act.get("url") or "").strip()
            if url:
                return url.rstrip("/")
    except (OSError, ValueError, ImportError):
        pass
    return None


def local_grok_cdp_reachable(
    *, display: str, app: str = "grok", timeout_s: float = 2.0
) -> bool:
    """True when ``/json/list`` succeeds for this display's configured CDP origin."""
    target = normalize_target(display, app)
    if not target.get("ok"):
        return False
    try:
        list_pages(str(target["origin"]), timeout_s=timeout_s)
    except (URLError, OSError, TimeoutError):
        return False
    return True


async def relay_grok_operator_paste_if_needed(
    req: DisplayChatPasteRequest,
) -> DisplayChatPasteResponse | None:
    """Forward paste to hub actuation when this satellite cannot reach Grok CDP.

    Transport errors before the hub receives the POST return ``debug_unreachable``
    (retriable). Errors after send may leave paste applied; those return
    ``outcome_unknown`` so the wake dispatcher does not duplicate paste.
    """
    from cdp_ask.display_chat_paste import _to_response

    if req.app != "grok":
        return None
    if local_grok_cdp_reachable(display=req.display, app=req.app):
        return None
    base = operator_actuation_paste_url()
    if not base:
        return None
    url = (
        base
        if base.endswith("/v1/display-chat/paste")
        else f"{base}/v1/display-chat/paste"
    )
    payload = req.model_dump()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        raw: dict[str, Any] = {
            "ok": False,
            "reason": "debug_unreachable",
            "detail": type(exc).__name__,
            "display": req.display,
            "app": req.app,
        }
        return _to_response(raw, req=req)
    except httpx.RequestError as exc:
        logger.warning(
            "Grok operator paste hub request failed after send: %s url=%s",
            type(exc).__name__,
            url,
        )
        raw = {
            "ok": False,
            "reason": "outcome_unknown",
            "detail": type(exc).__name__,
            "display": req.display,
            "app": req.app,
        }
        return _to_response(raw, req=req)
    if resp.status_code == 409:
        body = resp.json() if resp.content else {}
        raw = {
            "ok": False,
            "reason": str(body.get("code") or body.get("reason") or "refused"),
            **body,
        }
        return _to_response(raw, req=req)
    resp.raise_for_status()
    body = resp.json() if resp.content else {}
    if not isinstance(body, dict):
        body = {"ok": False, "reason": "protocol_error"}
    return _to_response(body, req=req)


__all__ = [
    "local_grok_cdp_reachable",
    "operator_actuation_paste_url",
    "relay_grok_operator_paste_if_needed",
]
