"""Bounded CSE harvest — no paste or submit. Opens chat_url when no lane is live."""

from __future__ import annotations

from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.cowork_output_download import resolve_harvest_body
from claude_bundles.cse_provenance import resolve as resolve_provenance
from claude_bundles.cse_provenance_resolve import is_row_present
from claude_bundles.cse_turns_harvest import harvest_turns
from claude_bundles.skills_ui_panel import connect_cdp

from cdp_ask.cse_session_ack import classify_ack
from cdp_ask.cse_session_events import (
    emit,
    mcp_cse_session_acknowledged,
    mcp_cse_session_harvested,
)
from cdp_ask.cse_session_harvest_identity import resolve_harvest_chat_url
from cdp_ask.cse_session_harvest_open import harvest_by_opening_url
from cdp_ask.cse_session_models import (
    CseSessionTurn,
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
    if req.source in {"output-file", "auto"}:
        try:
            preview = await harvest_turns(page, limit=1)
            chat_body = ""
            if preview.get("turns"):
                chat_body = str(preview["turns"][-1].get("text") or "")
            body_result = await resolve_harvest_body(
                page,
                chat_body,
                harvest_source=req.source,
                expected_size="auto",
                download_output=False,
            )
            if body_result and body_result.content:
                ack = classify_ack(
                    body_result.content,
                    marker=req.marker,
                    successor_birth_id=req.successor_birth_id,
                )
                return HarvestResponse(
                    outcome="harvested",
                    ack_class=ack,
                    turns=[
                        CseSessionTurn(
                            author="assistant",
                            text=body_result.content,
                            source="output-file",
                        )
                    ],
                    content_provenance=str(body_result.provenance),
                    provenance=provenance,
                )
        except Exception:
            if req.source == "output-file":
                return HarvestResponse(outcome="unreachable", reason="output_file_miss")
    try:
        dom = await harvest_turns(page, limit=limit, after_turn=req.after_turn)
    except Exception as exc:
        return HarvestResponse(outcome="unreachable", reason=str(exc))
    if dom.get("in_flight"):
        return HarvestResponse(
            outcome="streaming",
            provenance=provenance,
            streaming=bool(dom.get("streaming")),
            stop=bool(dom.get("stop")),
            tool_pause=bool(dom.get("tool_pause")),
        )
    if dom.get("incomplete_dom"):
        return HarvestResponse(outcome="incomplete_dom", provenance=provenance)
    turns = [
        CseSessionTurn(
            author=row["author"],
            timestamp=row.get("timestamp"),
            text=row["text"],
            source="cse-dom",
            ordinal=row.get("ordinal"),
        )
        for row in dom.get("turns") or []
    ]
    if not turns:
        return HarvestResponse(outcome="no_reply_yet", provenance=provenance)
    ack = classify_ack(
        turns[-1].text,
        marker=req.marker,
        successor_birth_id=req.successor_birth_id,
    )
    return HarvestResponse(
        outcome="harvested",
        ack_class=ack,
        turns=turns,
        truncated=bool(dom.get("truncated")),
        cursor=turns[-1].ordinal,
        content_provenance="cse-dom",
        provenance=provenance,
    )


async def _open_detached(
    chat_url: str,
    req: HarvestRequest,
    provenance: dict[str, Any] | None,
    registration_id: str | None,
) -> HarvestResponse:
    response = await harvest_by_opening_url(chat_url, req, provenance, harvest_page)
    return _emit(registration_id, response)


async def execute_harvest(
    req: HarvestRequest,
    store: ExecutionStore,
) -> HarvestResponse:
    """Harvest turns from a live lane, or open chat_url and scrape it."""
    registration_id, chat_url, provenance, early = await _resolve_target(req, store)
    url = (chat_url or req.chat_url or "").strip() or (
        await resolve_harvest_chat_url(req, store)
    ) or ""
    if early is not None:
        if url and early.outcome in {"not_attached", "dormant"}:
            return await _open_detached(url, req, early.provenance or provenance, registration_id)
        return _emit(registration_id, early)

    lane = next(
        (row for row in cdp_registry.list_active() if row.registration_id == registration_id),
        None,
    )
    if lane is None:
        if url:
            return await _open_detached(url, req, provenance, registration_id)
        return _emit(
            registration_id,
            HarvestResponse(outcome="not_attached", reason="lane_not_attached"),
        )

    try:
        pw, _browser, _ctx, page = await connect_cdp(lane.cdp_url)
        try:
            response = await harvest_page(page, req, provenance)
        finally:
            await pw.stop()
    except Exception as exc:
        return HarvestResponse(outcome="unreachable", reason=str(exc))
    return _emit(registration_id, response)
