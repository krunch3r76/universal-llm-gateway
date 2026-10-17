"""Bounded CSE harvest — no paste or submit. Opens chat_url when no lane is live."""

from __future__ import annotations

from typing import Any

from chat_harvest.models import ClassifyRefuse, classify_chat_url
from claude_bundles import cdp_registry
from claude_bundles.cse_identity_drift import evaluate_harvest_identity
from claude_bundles.cse_provenance import resolve as resolve_provenance
from claude_bundles.cse_provenance_resolve import is_row_present
from claude_bundles.skills_ui_panel import connect_cdp

from cdp_ask.cse_session_events import (
    emit,
    mcp_cse_session_acknowledged,
    mcp_cse_session_harvested,
)
from cdp_ask.cse_session_harvest_identity import (
    resolve_execution_provenance,
    resolve_harvest_chat_url,
)
from cdp_ask.cse_session_harvest_open import _LaneHold, harvest_by_opening_url
from cdp_ask.cse_session_harvest_scrape import (
    harvest_with_loading_wait,
    pick_page_for_chat_url,
)
from cdp_ask.cse_session_models import (
    HarvestIdentity,
    HarvestRequest,
    HarvestResponse,
)
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup_resolve import discover_candidates

HARVEST_HARD_CAP = 50

_DETACH_SWALLOW = frozenset(
    {"attachment.not_attached", "seat.open_on_detach", "standdown.missing"}
)


def _post_session_close(payload: dict[str, Any]) -> Any:
    """POST a session-close body to Cortex. Tests replace this."""
    import httpx
    from transport_utils import DEFAULT_CORTEX_URL

    url = f"{DEFAULT_CORTEX_URL.rstrip('/')}/sessions/close"
    with httpx.Client(timeout=30.0) as client:
        return client.post(url, json=payload)


async def _scrape_predecessor_turns(port: int, chat_url: str) -> list[dict[str, Any]]:
    """Scrape the live Cowork page for one predecessor port."""
    from claude_bundles.cse_turns_harvest import harvest_turns

    pw, _browser, ctx, page = await connect_cdp(f"http://127.0.0.1:{port}")
    try:
        page = await pick_page_for_chat_url(ctx, chat_url, fallback=page)
        raw = await harvest_turns(page, limit=HARVEST_HARD_CAP)
    finally:
        await pw.stop()
    turns = raw.get("turns") if isinstance(raw, dict) else None
    return [row for row in (turns or []) if isinstance(row, dict)]


def _succession_close_payload(
    row: dict[str, Any],
    *,
    registration_id: str,
    turns: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a SessionCloseRequest body that passes the verbatim close gates."""
    from datetime import UTC, datetime
    from urllib.parse import urlsplit

    from chat_harvest.messages import build_harvest_envelope, turns_to_messages
    from continuity_tape.messages import envelope_wire_dict

    parent_thread = str(row.get("parent_thread") or row.get("seat_lane") or "").strip()
    chat_url = str(row.get("chat_url") or "").strip()
    started_at = float(row.get("started_at") or row.get("seat_bound_at") or 0.0)
    cse_id = urlsplit(chat_url).path.rstrip("/").split("/")[-1] or registration_id[:8]
    opened = datetime.fromtimestamp(started_at, tz=UTC)
    session_id = f"web-anthropic-{opened:%Y-%m-%d-%H%M%S}-{registration_id[:3].lower()}"
    summary = (
        f"Succession harvest seal for Cowork seat {registration_id[:8]} "
        f"on lane {parent_thread}."
    )
    session_summary_md = (
        "## Session Summary\n\n"
        f"Predecessor Cowork CSE (`{chat_url}`, registration `{registration_id}`) "
        f"sealed to continuity tape before detach on lane `{parent_thread}`.\n\n"
        "**Decisions:** (absent — succession harvest before CSE detach)\n"
        "**Open items:** (absent — succession harvest before CSE detach)\n"
    )
    harvested_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    envelope = build_harvest_envelope(
        site="claude",
        conversation_id=cse_id,
        url=chat_url,
        messages=turns_to_messages(turns),
        harvested_at=harvested_at,
        streaming=False,
    )
    return {
        "session_id": session_id,
        "agent": "web-anthropic",
        "summary": summary,
        "session_summary_md": session_summary_md,
        "transcript_depth": "verbatim",
        "transcript_messages": envelope_wire_dict(envelope),
        "closed_by": "succession",
        "succession_seal_authority": True,
        "entity_ids": [f"agent-bus:{parent_thread}"],
        "stamp_thread": parent_thread,
        "stamp_surface": "claude_ai",
        "conversation_uuid": cse_id,
    }


async def seal_predecessor_cse_to_tape(
    registration_id: str,
    *,
    store: ExecutionStore | None = None,
) -> dict[str, Any]:
    """Seal a predecessor Cowork transcript onto continuity tape before detach.

    A listening Chrome port is scraped and posted to Cortex ``/sessions/close``.
    HTTP errors, timeouts, and a missing ``transcript_path`` raise
    ``standdown.tape_seal_failed`` and leave the listener up. A dead port
    returns a degraded dict so detach of a gone host can proceed. This function
    does not take ``ports_lock`` and does not detach. ``store`` is accepted for
    the detach route's call shape and is not required for the registry read.
    """
    from claude_bundles import cdp_lane
    from claude_bundles import cdp_registry_store as registry_store
    from universal_protocol.errors import ProtocolError

    del store
    rid = (registration_id or "").strip()
    row = registry_store.load_active().get(rid)
    if not isinstance(row, dict):
        return {"degraded": True, "registration_id": rid, "reason": "missing_row"}
    port = row.get("port")
    if not isinstance(port, int) or not cdp_lane.is_listening(port):
        return {
            "degraded": True,
            "registration_id": rid,
            "transcript_path": None,
            "reason": "listener_down",
        }
    chat_url = str(row.get("chat_url") or "").strip()
    try:
        turns = await _scrape_predecessor_turns(port, chat_url)
        from chat_harvest.archive import archive_chat_transcript
        from chat_harvest.models import ChatTurn

        cse_id = chat_url.rstrip("/").split("/")[-1]
        archive_turns = [
            ChatTurn(
                author=row_turn.get("author") or "assistant",
                ordinal=int(row_turn.get("ordinal") or index + 1),
                text=str(row_turn.get("text") or ""),
                source="dom",
            )
            for index, row_turn in enumerate(turns)
            if row_turn.get("author") in {"user", "assistant"}
        ]
        archive_chat_transcript(
            site="claude",
            conversation_id=cse_id,
            url=chat_url,
            turns=archive_turns,
            streaming=False,
        )
        payload = _succession_close_payload(row, registration_id=rid, turns=turns)
        response = _post_session_close(payload)
    except ProtocolError:
        raise
    except Exception as exc:
        raise ProtocolError(
            code="standdown.tape_seal_failed",
            message=f"predecessor tape seal failed: {type(exc).__name__}: {exc}",
            source="rpc",
            retryable=True,
            data={"registration_id": rid},
        ) from exc
    status = getattr(response, "status_code", None)
    body: dict[str, Any] = {}
    if status == 200:
        try:
            parsed = response.json()
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            body = parsed
    path = str(body.get("transcript_path") or "").strip()
    already = bool(body.get("already_closed"))
    journal_row_id = body.get("journal_row_id")
    if status != 200 or not path or (journal_row_id in (None, "") and not already):
        raise ProtocolError(
            code="standdown.tape_seal_failed",
            message="stand-down tape seal failed before detach",
            source="rpc",
            retryable=True,
            data={
                "registration_id": rid,
                "status_code": status,
                "transcript_path": path or None,
            },
        )
    return {
        "registration_id": rid,
        "journal_row_id": journal_row_id,
        "transcript_path": path,
        "already_closed": already,
    }


async def _retire_closed_seat_after_typed_ack(
    registration_id: str | None,
    response: HarvestResponse,
) -> None:
    """Stamp, seal, then detach a closed seat after a typed stand-down ACK."""
    if response.outcome != "harvested" or response.ack_class != "typed_ack":
        return
    rid = (registration_id or "").strip()
    if not rid:
        return
    from claude_bundles import cdp_registry_store as registry_store
    from claude_bundles.cdp_registry.models import seat_open
    from universal_protocol.errors import ProtocolError

    from cdp_ask.cse_session_paste import _append_standdown_token

    row = registry_store.load_active().get(rid)
    if not isinstance(row, dict) or seat_open(row):
        return
    _append_standdown_token(
        registration_id=rid,
        chat_url=str(row.get("chat_url") or response.chat_url or ""),
        envelope="stand_down",
        pasted=True,
        receipt={"ack_class": "typed_ack"},
        error=None,
        detail=None,
    )
    try:
        await seal_predecessor_cse_to_tape(rid)
    except ProtocolError as exc:
        if exc.code == "standdown.tape_seal_failed":
            return
        raise
    try:
        cdp_registry.detach(rid, reason="standdown_ack_harvested")
    except ProtocolError as exc:
        if exc.code in _DETACH_SWALLOW:
            return
        raise


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
        return (
            None,
            None,
            None,
            HarvestResponse(
                outcome="conflict",
                reason="ambiguous_identity",
                provenance={
                    "candidates": [c.as_info().model_dump() for c in candidates]
                },
            ),
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
                return dormant.registration_id, dormant.chat_url, prov, None
        return (
            None,
            None,
            None,
            HarvestResponse(outcome="not_attached", reason="no_target"),
        )
    chosen = candidates[0]
    return chosen.registration_id, chosen.chat_url, chosen.provenance, None


def _bind_chat_url(response: HarvestResponse, chat_url: str) -> HarvestResponse:
    """Copy the resolved Cowork URL onto the payload so recycle harvests stay quotable."""
    token = (chat_url or "").strip()
    if token and not response.chat_url:
        response.chat_url = token
    return response


def _registration_id_from_provenance(
    provenance: dict[str, Any] | None,
    fallback: str | None,
) -> str | None:
    prov = provenance if isinstance(provenance, dict) else {}
    current = str(prov.get("registration_id") or fallback or "").strip()
    return current or None


def _apply_harvest_identity(
    response: HarvestResponse,
    *,
    requested_registration_id: str | None,
    requested_chat_url: str | None,
    observed_chat_url: str | None,
    current_registration_id: str | None,
) -> HarvestResponse:
    identity_data = evaluate_harvest_identity(
        requested_chat_url=requested_chat_url,
        requested_registration_id=requested_registration_id,
        observed_chat_url=observed_chat_url,
        current_registration_id=current_registration_id,
    )
    response.identity = HarvestIdentity.model_validate(identity_data)
    if response.identity.identity_check == "foreign_transcript":
        return HarvestResponse(
            outcome="conflict",
            reason="foreign_transcript",
            turns=[],
            provenance=response.provenance,
            chat_url=response.chat_url,
            identity=response.identity,
        )
    return response


def _emit(registration_id: str | None, response: HarvestResponse) -> HarvestResponse:
    if response.ack_class == "typed_ack":
        emit(
            mcp_cse_session_acknowledged(
                registration_id=registration_id,
                ack_class=response.ack_class,
            )
        )
    identity_check = (
        response.identity.identity_check if response.identity is not None else None
    )
    emit(
        mcp_cse_session_harvested(
            registration_id=registration_id,
            outcome=response.outcome,
            ack_class=response.ack_class,
            turn_count=len(response.turns),
            reason=response.reason,
            waited_ms=response.waited_ms,
            identity_check=identity_check,
        )
    )
    return response


async def harvest_page(
    page: Any,
    req: HarvestRequest,
    provenance: dict[str, Any] | None,
    *,
    requested_registration_id: str | None = None,
    requested_chat_url: str | None = None,
) -> HarvestResponse:
    """Scrape an already-open CSE page (attached lane or just-opened URL)."""
    current_reg = _registration_id_from_provenance(
        provenance, requested_registration_id
    )
    if req.metadata_only:
        response = HarvestResponse(
            outcome="harvested",
            provenance=provenance,
            content_provenance="metadata_only",
        )
        return _apply_harvest_identity(
            response,
            requested_registration_id=requested_registration_id,
            requested_chat_url=requested_chat_url,
            observed_chat_url=None,
            current_registration_id=current_reg,
        )
    limit = min(int(req.limit), HARVEST_HARD_CAP)
    response = await harvest_with_loading_wait(page, req, provenance, limit=limit)
    page_url = str(getattr(page, "url", None) or "").strip() or None
    return _apply_harvest_identity(
        response,
        requested_registration_id=requested_registration_id,
        requested_chat_url=requested_chat_url,
        observed_chat_url=page_url,
        current_registration_id=_registration_id_from_provenance(
            response.provenance,
            current_reg,
        ),
    )


async def _open_detached(
    chat_url: str,
    req: HarvestRequest,
    provenance: dict[str, Any] | None,
    registration_id: str | None,
    *,
    requested_registration_id: str | None = None,
    requested_chat_url: str | None = None,
) -> HarvestResponse:
    response = await harvest_by_opening_url(
        chat_url,
        req,
        provenance,
        harvest_page,
        requested_registration_id=requested_registration_id,
        requested_chat_url=requested_chat_url or chat_url,
    )
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


async def _refuse_unresolved_lineage(
    execution_id: str,
    store: ExecutionStore,
) -> HarvestResponse | None:
    """Refuse harvest when the requested execution's lineage cannot be proven."""
    provenance = await resolve_execution_provenance(execution_id, store)
    if provenance is None:
        return None
    if provenance.get("lineage_state") != "unresolved":
        return None
    return HarvestResponse(
        outcome="refused",
        reason="lineage_unresolved",
        provenance=provenance,
        chat_url=provenance.get("chat_url"),
    )


async def execute_harvest(
    req: HarvestRequest,
    store: ExecutionStore,
) -> HarvestResponse:
    """Harvest turns from a live lane, or open chat_url and scrape it."""
    requested_registration_id = (req.registration_id or "").strip() or None
    execution_id = (req.execution_id or "").strip() or None
    if execution_id:
        if refused := await _refuse_unresolved_lineage(execution_id, store):
            reg = str((refused.provenance or {}).get("registration_id") or "").strip()
            return _emit(reg or None, refused)
    if refused := _refuse_product_chat_url(req.chat_url or ""):
        return _emit(None, refused)
    registration_id, chat_url, provenance, early = await _resolve_target(req, store)
    if refused := _refuse_product_chat_url(chat_url or ""):
        return _emit(registration_id, refused)
    url = (
        (chat_url or req.chat_url or "").strip()
        or (await resolve_harvest_chat_url(req, store))
        or ""
    )
    requested_chat_url = url or None
    identity_kwargs = {
        "requested_registration_id": requested_registration_id,
        "requested_chat_url": requested_chat_url,
    }
    if early is not None:
        if url and early.outcome == "not_attached":
            return _bind_chat_url(
                await _open_detached(
                    url,
                    req,
                    early.provenance or provenance,
                    registration_id,
                    **identity_kwargs,
                ),
                url,
            )
        return _bind_chat_url(_emit(registration_id, early), url)

    lane = next(
        (
            row
            for row in cdp_registry.list_active()
            if row.registration_id == registration_id
        ),
        None,
    )
    if lane is None:
        if url:
            return _bind_chat_url(
                await _open_detached(
                    url,
                    req,
                    provenance,
                    registration_id,
                    **identity_kwargs,
                ),
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
        async with _LaneHold(registration_id):
            pw, _browser, ctx, page = await connect_cdp(lane.cdp_url)
            if url:
                page = await pick_page_for_chat_url(ctx, url, fallback=page)
            try:
                response = await harvest_page(
                    page,
                    req,
                    provenance,
                    **identity_kwargs,
                )
            finally:
                await pw.stop()
    except Exception as exc:
        return _bind_chat_url(
            HarvestResponse(outcome="unreachable", reason=str(exc)),
            url,
        )
    bound = _bind_chat_url(_emit(registration_id, response), url)
    await _retire_closed_seat_after_typed_ack(registration_id, bound)
    return bound
