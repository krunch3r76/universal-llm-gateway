"""Bounded CSE harvest — no paste or submit. Opens chat_url when no lane is live."""

from __future__ import annotations

from typing import Any

from chat_harvest.models import ClassifyRefuse, classify_chat_url
from claude_bundles import cdp_registry
from claude_bundles.cse_provenance import resolve as resolve_provenance
from claude_bundles.cse_provenance_resolve import is_row_present
from claude_bundles.skills_ui_panel import connect_cdp

from cdp_ask.cse_session_events import (
    emit,
    mcp_cse_session_acknowledged,
    mcp_cse_session_harvested,
)
from cdp_ask.cse_session_harvest_identity import resolve_harvest_chat_url
from cdp_ask.cse_session_harvest_open import harvest_by_opening_url
from cdp_ask.cse_session_harvest_scrape import (
    harvest_with_loading_wait,
    pick_page_for_chat_url,
)
from cdp_ask.cse_session_models import (
    HarvestRequest,
    HarvestResponse,
)
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup_resolve import discover_candidates

HARVEST_HARD_CAP = 50


async def _resolve_target(
    req: HarvestRequest,
    store: ExecutionStore,
) -> tuple[str | None, str | None, dict[str, Any] | None, HarvestResponse | None]:
    from cdp_ask.models import FollowupProjectAskRequest

    followup_req = FollowupProjectAskRequest(
        chat_url=req.chat_url,
        registration_id=req.registration_id,
        execution_id=req.execution_id,
    )
    candidates, _path, _exec = await discover_candidates(followup_req, store)
    if len(candidates) > 1:
        return None, None, None, HarvestResponse(
            outcome="conflict",
            reason="ambiguous_identity",
            provenance={"candidates": [c.as_info().model_dump() for c in candidates]},
        )
    if not candidates:
        chat = (req.chat_url or "").strip()
        if chat:
            dormant = cdp_registry.dormant_for_chat_url(chat)
            if dormant is not None:
                prov = resolve_provenance(
                    chat_url=dormant.chat_url,
                    registration_id=dormant.registration_id,
                    host_listable=is_row_present,
                )
                return (
                    dormant.registration_id,
                    dormant.chat_url,
                    prov,
                    HarvestResponse(outcome="dormant", provenance=prov),
                )
        return None, None, None, HarvestResponse(outcome="not_attached", reason="no_target")
    chosen = candidates[0]
    return chosen.registration_id, chosen.chat_url, chosen.provenance, None


def _bind_chat_url(response: HarvestResponse, chat_url: str) -> HarvestResponse:
    """Copy the resolved Cowork URL onto the payload so recycle harvests stay quotable."""
    token = (chat_url or "").strip()
    if token and not response.chat_url:
        response.chat_url = token
    return response


def _emit(registration_id: str | None, response: HarvestResponse) -> HarvestResponse:
    if response.ack_class == "typed_ack":
        emit(
            mcp_cse_session_acknowledged(
                registration_id=registration_id,
                ack_class=response.ack_class,
            )
        )
    emit(
        mcp_cse_session_harvested(
            registration_id=registration_id,
            outcome=response.outcome,
            ack_class=response.ack_class,
            turn_count=len(response.turns),
            reason=response.reason,
            waited_ms=response.waited_ms,
        )
    )
    return response


async def harvest_page(
    page: Any,
    req: HarvestRequest,
    provenance: dict[str, Any] | None,
) -> HarvestResponse:
    """Scrape an already-open CSE page (attached lane or just-opened URL)."""
    if req.metadata_only:
        return HarvestResponse(
            outcome="harvested",
            provenance=provenance,
            content_provenance="metadata_only",
        )
    limit = min(int(req.limit), HARVEST_HARD_CAP)
    return await harvest_with_loading_wait(page, req, provenance, limit=limit)


async def _open_detached(
    chat_url: str,
    req: HarvestRequest,
    provenance: dict[str, Any] | None,
    registration_id: str | None,
) -> HarvestResponse:
    response = await harvest_by_opening_url(chat_url, req, provenance, harvest_page)
    return _emit(registration_id, response)


def _refuse_product_chat_url(chat_url: str) -> HarvestResponse | None:
    classified = classify_chat_url(chat_url)
    if isinstance(classified, ClassifyRefuse):
        return None
    token = (chat_url or "").strip()
    if not token or classified.site != "claude" or not classified.conversation_id:
        return None
    if "/chat/" not in token.lower():
        return None
    return HarvestResponse(
        outcome="refused",
        reason="product-chat URLs use chat_session",
        code="use_chat_session",
        chat_url=token,
    )


async def execute_harvest(
    req: HarvestRequest,
    store: ExecutionStore,
) -> HarvestResponse:
    """Harvest turns from a live lane, or open chat_url and scrape it."""
    if refused := _refuse_product_chat_url(req.chat_url or ""):
        return _emit(None, refused)
    registration_id, chat_url, provenance, early = await _resolve_target(req, store)
    if refused := _refuse_product_chat_url(chat_url or ""):
        return _emit(registration_id, refused)
    url = (chat_url or req.chat_url or "").strip() or (
        await resolve_harvest_chat_url(req, store)
    ) or ""
    if early is not None:
        if url and early.outcome in {"not_attached", "dormant"}:
            return _bind_chat_url(
                await _open_detached(
                    url, req, early.provenance or provenance, registration_id
                ),
                url,
            )
        return _bind_chat_url(_emit(registration_id, early), url)

    lane = next(
        (row for row in cdp_registry.list_active() if row.registration_id == registration_id),
        None,
    )
    if lane is None:
        if url:
            return _bind_chat_url(
                await _open_detached(url, req, provenance, registration_id),
                url,
            )
        return _bind_chat_url(
            _emit(
                registration_id,
                HarvestResponse(outcome="not_attached", reason="lane_not_attached"),
            ),
            url,
        )

    try:
        pw, _browser, ctx, page = await connect_cdp(lane.cdp_url)
        if url:
            page = await pick_page_for_chat_url(ctx, url, fallback=page)
        try:
            response = await harvest_page(page, req, provenance)
        finally:
            await pw.stop()
    except Exception as exc:
        return _bind_chat_url(
            HarvestResponse(outcome="unreachable", reason=str(exc)),
            url,
        )
    return _bind_chat_url(_emit(registration_id, response), url)
