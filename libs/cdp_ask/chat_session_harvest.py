"""Product-chat harvest and probe — site-neutral, no CSE lane identity."""

from __future__ import annotations

from chat_harvest.grok_adapter import execute_grok_harvest
from chat_harvest.models import ClassifyRefuse, classify_chat_url
from universal_logging import get_logger
from web_chat_relay.grok_session import DEFAULT_CDP_URL

from cdp_ask.chat_session_events import emit, mcp_chat_session_harvested
from cdp_ask.chat_session_models import ChatHarvestRequest, ChatHarvestResponse

logger = get_logger(__name__)


def _emit_harvested(response: ChatHarvestResponse) -> None:
    if not response.site:
        return
    emit(
        mcp_chat_session_harvested(
            site=response.site,
            conversation_id=response.conversation_id or "",
            outcome=response.outcome,
            turn_count=response.turn_count,
            archive_uri=response.archive_uri,
            archive_sha256=response.archive_sha256,
            code=response.code,
        )
    )


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
        if not req.metadata_only:
            _emit_harvested(result)
        return result

    logger.info(
        "chat_session harvest claude pending site=%s conversation_id=%s",
        classified.site,
        classified.conversation_id,
    )
    return ChatHarvestResponse(
        outcome="refused",
        site=classified.site,
        conversation_id=classified.conversation_id,
        url=classified.url,
        code="claude_adapter_pending",
        reason="claude /chat/ adapter not implemented in this slice",
    )


async def execute_probe(req: ChatHarvestRequest) -> ChatHarvestResponse:
    """Metadata-only probe — no sidecar write and no Event Service signal."""
    probe_req = req.model_copy(update={"metadata_only": True})
    return await execute_harvest(probe_req)
