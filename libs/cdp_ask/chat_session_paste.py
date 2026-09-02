"""Grant-gated product-chat paste — grok adapter in this slice."""

from __future__ import annotations

from chat_harvest.claude_chat_adapter import execute_claude_paste
from chat_harvest.grok_adapter import execute_grok_paste
from chat_harvest.models import ClassifyRefuse, classify_chat_url
from web_chat_relay.grok_session import DEFAULT_CDP_URL

from cdp_ask.chat_session_events import emit, mcp_chat_session_pasted
from cdp_ask.chat_session_models import ChatPasteRequest, ChatPasteResponse
from cdp_ask.runner import resolve_followup_prompt


def _emit_pasted(response: ChatPasteResponse) -> None:
    if not response.site:
        return
    emit(
        mcp_chat_session_pasted(
            site=response.site,
            conversation_id=response.conversation_id or "",
            ok=response.ok,
            url=response.url,
            archive_uri=response.archive_uri,
            archive_sha256=response.archive_sha256,
            code=response.code,
        )
    )


async def execute_paste(req: ChatPasteRequest) -> ChatPasteResponse:
    """Paste after classifier + grant gates; emit pasted once both pass."""
    classified = classify_chat_url(req.url, site=req.site)
    if isinstance(classified, ClassifyRefuse):
        return ChatPasteResponse(
            ok=False,
            code=classified.code,
            reason=classified.reason,
        )

    grant = getattr(req, "grant", None)
    if grant not in ("explicit", "operator"):
        return ChatPasteResponse(
            ok=False,
            code="grant_required",
            reason="grant must be explicit or operator",
        )

    cdp_url = (req.cdp_url or "").strip() or DEFAULT_CDP_URL

    if classified.site == "grok":
        try:
            prompt_text = resolve_followup_prompt(req)
        except ValueError as exc:
            return ChatPasteResponse(
                ok=False,
                code="prompt_required",
                reason=str(exc),
            )
        result = await execute_grok_paste(
            url=classified.url,
            prompt_text=prompt_text,
            cdp_url=cdp_url,
            grant=grant,
        )
        if result.code != "relay_lock_fresh":
            _emit_pasted(result)
        return result

    if classified.site == "claude":
        try:
            prompt_text = resolve_followup_prompt(req)
        except ValueError as exc:
            return ChatPasteResponse(
                ok=False,
                code="prompt_required",
                reason=str(exc),
            )
        result = await execute_claude_paste(
            url=classified.url,
            prompt_text=prompt_text,
            cdp_url=cdp_url,
            grant=grant,
        )
        _emit_pasted(result)
        return result

    return ChatPasteResponse(
        ok=False,
        site=classified.site,
        conversation_id=classified.conversation_id,
        url=classified.url,
        code="unsupported_site",
        reason=f"unsupported site for paste: {classified.site!r}",
    )
