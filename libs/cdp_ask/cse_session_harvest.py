"""Bounded read-only CSE harvest — no submit, followup, abort, or Chrome relaunch."""

from __future__ import annotations

from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.cowork_output_download import resolve_harvest_body
from claude_bundles.cse_provenance import resolve as resolve_provenance
from claude_bundles.cse_provenance_resolve import is_host_listable
from claude_bundles.cse_turns_harvest import harvest_turns
from claude_bundles.skills_ui_panel import connect_cdp

from cdp_ask.cse_session_ack import classify_ack
from cdp_ask.cse_session_events import (
    emit,
    mcp_cse_session_acknowledged,
    mcp_cse_session_harvested,
)
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
                    host_listable=is_host_listable,
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


async def execute_harvest(
    req: HarvestRequest,
    store: ExecutionStore,
) -> HarvestResponse:
    """Harvest bounded turns from an attached lane without side effects."""
    limit = min(int(req.limit), HARVEST_HARD_CAP)
    registration_id, chat_url, provenance, early = await _resolve_target(req, store)
    if early is not None:
        emit(
            mcp_cse_session_harvested(
                registration_id=registration_id,
                outcome=early.outcome,
                ack_class=early.ack_class,
            )
        )
        return early

    lane = next(
        (row for row in cdp_registry.list_active() if row.registration_id == registration_id),
        None,
    )
    if lane is None:
        dormant = cdp_registry.dormant_for_chat_url(chat_url or "")
        if dormant is not None:
            response = HarvestResponse(
                outcome="dormant",
                provenance=provenance,
            )
            emit(
                mcp_cse_session_harvested(
                    registration_id=dormant.registration_id,
                    outcome="dormant",
                    ack_class="no_proof",
                )
            )
            return response
        return HarvestResponse(outcome="not_attached", reason="lane_not_attached")

    if req.metadata_only:
        response = HarvestResponse(
            outcome="harvested",
            provenance=provenance,
            content_provenance="metadata_only",
        )
        emit(
            mcp_cse_session_harvested(
                registration_id=registration_id,
                outcome="harvested",
                ack_class="no_proof",
                turn_count=0,
            )
        )
        return response

    if req.source in {"output-file", "auto"}:
        try:
            pw, _browser, _ctx, page = await connect_cdp(lane.cdp_url)
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
            finally:
                await pw.stop()
            if body_result and body_result.content:
                ack = classify_ack(
                    body_result.content,
                    marker=req.marker,
                    successor_birth_id=req.successor_birth_id,
                )
                turn = CseSessionTurn(
                    author="assistant",
                    text=body_result.content,
                    source="output-file",
                )
                response = HarvestResponse(
                    outcome="harvested",
                    ack_class=ack,
                    turns=[turn],
                    content_provenance=str(body_result.provenance),
                    provenance=provenance,
                )
                if ack == "typed_ack":
                    emit(
                        mcp_cse_session_acknowledged(
                            registration_id=registration_id,
                            ack_class=ack,
                        )
                    )
                emit(
                    mcp_cse_session_harvested(
                        registration_id=registration_id,
                        outcome="harvested",
                        ack_class=ack,
                        turn_count=1,
                    )
                )
                return response
        except Exception:
            if req.source == "output-file":
                return HarvestResponse(outcome="unreachable", reason="output_file_miss")

    try:
        pw, _browser, _ctx, page = await connect_cdp(lane.cdp_url)
        try:
            dom = await harvest_turns(page, limit=limit, after_turn=req.after_turn)
        finally:
            await pw.stop()
    except Exception as exc:
        return HarvestResponse(outcome="unreachable", reason=str(exc))

    if dom.get("in_flight"):
        response = HarvestResponse(
            outcome="streaming",
            provenance=provenance,
            streaming=bool(dom.get("streaming")),
            stop=bool(dom.get("stop")),
            tool_pause=bool(dom.get("tool_pause")),
        )
        emit(
            mcp_cse_session_harvested(
                registration_id=registration_id,
                outcome="streaming",
                ack_class="no_proof",
            )
        )
        return response

    if dom.get("incomplete_dom"):
        response = HarvestResponse(
            outcome="incomplete_dom",
            provenance=provenance,
        )
        emit(
            mcp_cse_session_harvested(
                registration_id=registration_id,
                outcome="incomplete_dom",
                ack_class="no_proof",
            )
        )
        return response

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
        response = HarvestResponse(outcome="no_reply_yet", provenance=provenance)
    else:
        latest_text = turns[-1].text
        ack = classify_ack(
            latest_text,
            marker=req.marker,
            successor_birth_id=req.successor_birth_id,
        )
        response = HarvestResponse(
            outcome="harvested",
            ack_class=ack,
            turns=turns,
            truncated=bool(dom.get("truncated")),
            cursor=turns[-1].ordinal if turns else None,
            content_provenance="cse-dom",
            provenance=provenance,
        )
        if ack == "typed_ack":
            emit(
                mcp_cse_session_acknowledged(
                    registration_id=registration_id,
                    ack_class=ack,
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
