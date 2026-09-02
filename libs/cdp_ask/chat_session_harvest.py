"""Product-chat harvest and probe — site-neutral, no CSE lane identity."""

from __future__ import annotations

from chat_harvest.claude_chat_adapter import execute_claude_harvest
from chat_harvest.grok_adapter import execute_grok_harvest
from chat_harvest.models import ClassifyRefuse, classify_chat_url
from web_chat_relay.grok_session import DEFAULT_CDP_URL

from cdp_ask.chat_session_events import emit, mcp_chat_session_harvested
from cdp_ask.chat_session_models import ChatHarvestRequest, ChatHarvestResponse


def _emit_harvested(response: ChatHarvestResponse) -> None:
    if not response.site:
        return
    conflict_ordinal = response.conflict.ordinal if response.conflict else None
    emit(
        mcp_chat_session_harvested(
            site=response.site,
            conversation_id=response.conversation_id or "",
            outcome=response.outcome,
            turn_count=response.turn_count,
            archive_uri=response.archive_uri,
            archive_sha256=response.archive_sha256,
            code=response.code,
            conflict_ordinal=conflict_ordinal,
            opened_on_demand=response.opened_on_demand,
        )
    )


def _should_emit_harvested(req: ChatHarvestRequest, response: ChatHarvestResponse) -> bool:
    return not req.metadata_only or response.opened_on_demand


async def execute_harvest(req: ChatHarvestRequest) -> ChatHarvestResponse:
    """Full harvest: classify, attach, archive transcript, optional inline view."""
    classified = classify_chat_url(req.url, site=req.site)
    if isinstance(classified, ClassifyRefuse):
        return ChatHarvestResponse(
            outcome="refused",
            code=classified.code,
            reason=classified.reason,
        )

    if not classified.conversation_id:
        return ChatHarvestResponse(
            outcome="no_conversation",
            site=classified.site,
            conversation_id="",
            url=classified.url,
        )

    cdp_url = (req.cdp_url or "").strip() or DEFAULT_CDP_URL

    if classified.site == "grok":
        result = await execute_grok_harvest(
            url=classified.url,
            site=classified.site,
            conversation_id=classified.conversation_id,
            cdp_url=cdp_url,
            include_turns=req.include_turns,
            limit=req.limit,
            after_turn=req.after_turn,
            supersede=req.supersede,
            metadata_only=req.metadata_only,
        )
        if _should_emit_harvested(req, result):
            _emit_harvested(result)
        return result

    if classified.site == "claude":
        result = await execute_claude_harvest(
            url=classified.url,
            site=classified.site,
            conversation_id=classified.conversation_id,
            cdp_url=cdp_url,
            include_turns=req.include_turns,
            limit=req.limit,
            after_turn=req.after_turn,
            supersede=req.supersede,
            metadata_only=req.metadata_only,
        )
        if _should_emit_harvested(req, result):
            _emit_harvested(result)
        return result

    return ChatHarvestResponse(
        outcome="refused",
        site=classified.site,
        conversation_id=classified.conversation_id,
        url=classified.url,
        code="unsupported_site",
        reason=f"unsupported site for harvest: {classified.site!r}",
    )


async def execute_probe(req: ChatHarvestRequest) -> ChatHarvestResponse:
    """Metadata-only probe — no archive; emits when open-on-demand mints a tab."""
    probe_req = req.model_copy(update={"metadata_only": True})
    return await execute_harvest(probe_req)
