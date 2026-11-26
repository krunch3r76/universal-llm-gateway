"""Product-chat DOM observation orchestrator — classify, validate, thread offload, emit."""

from __future__ import annotations

import asyncio
from typing import Any

from chat_harvest.dom_observation import (
    ChatObserveRequest,
    InvalidCdpUrl,
    InvalidPattern,
    run_dom_observation,
    validate_cdp_url,
    validate_match_terms,
    validate_max_nodes,
)
from chat_harvest.models import ClassifyOk, ClassifyRefuse, classify_chat_url
from web_chat_relay.grok_session import DEFAULT_CDP_URL

from cdp_ask.chat_session_events import emit, mcp_chat_session_observed


def invalid_pattern_body(exc: InvalidPattern) -> dict[str, Any]:
    return {
        "code": "invalid_pattern",
        "message": exc.message,
        "source": "gateway",
        "retryable": False,
        "data": {"field": exc.field, "limit": exc.limit, "got": exc.got},
    }


def invalid_cdp_url_body(message: str = "invalid_cdp_url") -> dict[str, Any]:
    return {
        "code": "invalid_cdp_url",
        "message": message,
        "source": "gateway",
        "retryable": False,
        "data": {},
    }


def _refused_body(
    *,
    code: str,
    reason: str,
    url: str,
    site: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    return {
        "outcome": "refused",
        "code": code,
        "reason": reason,
        "site": site,
        "conversation_id": conversation_id,
        "url": url,
        "opened_on_demand": False,
        "observation": {
            "looked": False,
            "source": "dom.observation",
            "content_kind": "untrusted_page_text",
        },
    }


async def execute_observe(req: ChatObserveRequest) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (200 body, None) or (None, 409/refuse envelope)."""
    classified = classify_chat_url(req.url, site=req.site)
    if isinstance(classified, ClassifyRefuse):
        if classified.code == "use_cse_session":
            return None, {
                "code": classified.code,
                "message": classified.reason,
                "source": "gateway",
                "retryable": False,
                "data": {},
            }
        return _refused_body(
            code=classified.code,
            reason=classified.reason,
            url=req.url.strip(),
        ), None

    assert isinstance(classified, ClassifyOk)
    if classified.site == "grok":
        return _refused_body(
            code="unsupported_site",
            reason="grok URLs are not supported for dom observation",
            url=classified.url,
            site="grok",
            conversation_id=classified.conversation_id,
        ), None

    if not classified.conversation_id:
        return {
            "outcome": "no_conversation",
            "site": classified.site,
            "conversation_id": "",
            "url": classified.url,
            "opened_on_demand": False,
            "observation": {
                "looked": False,
                "source": "dom.observation",
                "content_kind": "untrusted_page_text",
            },
        }, None

    try:
        terms = validate_match_terms(req.match_terms)
    except InvalidPattern as exc:
        return None, invalid_pattern_body(exc)

    try:
        validate_max_nodes(req.max_nodes)
    except InvalidPattern as exc:
        return None, invalid_pattern_body(exc)

    cdp_raw = (req.cdp_url or "").strip() or DEFAULT_CDP_URL
    try:
        cdp_url = validate_cdp_url(cdp_raw)
    except InvalidCdpUrl:
        return None, invalid_cdp_url_body()

    scope = f"conversation:{classified.conversation_id}"
    result = await asyncio.to_thread(
        run_dom_observation,
        chat_url=classified.url,
        conversation_id=classified.conversation_id,
        cdp_url=cdp_url,
        match_terms=terms,
        max_nodes=req.max_nodes,
        open_on_demand=req.open_on_demand,
        scope=scope,
    )
    result["site"] = classified.site
    result.setdefault("conversation_id", classified.conversation_id)
    result.setdefault("url", classified.url)
    _emit_observed(result)
    return result, None


def _emit_observed(result: dict[str, Any]) -> None:
    observation = result.get("observation") or {}
    nodes_returned = 0
    counts = observation.get("counts")
    if isinstance(counts, dict):
        nodes_returned = int(counts.get("returned") or counts.get("total") or 0)
    looked = bool(observation.get("looked"))
    payload_code = result.get("code")
    opened = bool(result.get("opened_on_demand"))
    emit(
        mcp_chat_session_observed(
            site=str(result.get("site") or ""),
            conversation_id=str(result.get("conversation_id") or ""),
            outcome=str(result.get("outcome") or ""),
            looked=looked,
            nodes_returned=nodes_returned,
            code=payload_code if payload_code else None,
            opened_on_demand=opened,
        )
    )
