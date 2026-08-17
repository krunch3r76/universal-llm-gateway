"""Authorization-gated CSE paste with idempotent replay.

Callers: MCP ``cse_session`` paste relay and the cdp-ask ``/v1/cse-session/paste``
route. Hop-pair request fields, hop-watch predecessor stand_down, or an explicit
grant token must pass before DOM followup; unauthorized cross-lane paste fails
closed.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from claude_bundles.cse_provenance import resolve as resolve_provenance
from claude_bundles.cse_provenance_resolve import is_host_listable

from cdp_ask.cse_session_events import (
    emit,
    mcp_cse_session_conflict,
    mcp_cse_session_pasted,
)
from cdp_ask.cse_session_models import PasteRequest, PasteResponse
from cdp_ask.cse_session_provenance import self_supersession
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup import execute_followup
from cdp_ask.followup_receipts import receipt_meets
from cdp_ask.models import FollowupProjectAskRequest

_IDEMPOTENCY: dict[tuple[str, str], dict[str, Any]] = {}


def _idempotency_key(req: PasteRequest, *, target_registration_id: str) -> str:
    explicit = (req.idempotency_key or "").strip()
    if explicit:
        return explicit
    digest = hashlib.sha256(
        f"{target_registration_id}:{req.prompt_text or ''}:{req.prompt_uri or ''}".encode()
    ).hexdigest()
    return digest


def _protocol_error(code: str, *, detail: str | None = None) -> PasteResponse:
    return PasteResponse(
        ok=False,
        code=code,
        error=code,
        detail=detail,
        send_verified=False,
    )


def _parent_thread_of(prov: dict[str, Any]) -> str:
    """Prefer proven parent thread; fall back to the CSE's own claim."""
    return str(
        prov.get("parent_thread_proven") or prov.get("parent_thread_claim") or ""
    ).strip()


def _watch_authorizes_predecessor_stand_down(
    req: PasteRequest,
    *,
    target_reg: str,
    target_prov: dict[str, Any],
) -> bool:
    """True when hop already recorded *target_reg* as the superseded predecessor.

    Continuity hop writes ``superseded_registration_id`` onto the watch ledger at
    fire time — before the successor CSE exists — so paste cannot demand a
    request-time hop-pair triple the successor does not yet hold. ``stand_down``
    into that recorded predecessor is the designed retire path; free/page paste
    still requires the explicit hop-pair fields or a grant token.
    """
    if (req.envelope or "").strip().lower() != "stand_down":
        return False
    if not target_reg:
        return False
    from claude_bundles.hop_seat_cutover import load_watches

    watches = load_watches()
    parent = _parent_thread_of(target_prov) or (req.parent_thread or "").strip()
    rows: list[dict[str, Any]] = []
    if parent and parent in watches:
        rows.append(watches[parent])
    else:
        rows.extend(watches.values())
    for row in rows:
        superseded = str(row.get("superseded_registration_id") or "").strip()
        if superseded and superseded == target_reg:
            return True
    return False


def _same_lane_authorizes_stand_down(
    req: PasteRequest,
    *,
    target_prov: dict[str, Any],
) -> bool:
    """True when a same-lane claimant stands down another CSE on that lane.

    Hop watch records the captured predecessor registration, which may not be
    either live operator CSE (thread 7437 watch named ``d3402e55…`` while the
    colliding seats were distinct ids). ``stand_down`` plus caller identity and
    matching parent_thread provenance is the in-band retire path for that
    collision; free/page paste still requires the hop-pair triple or a grant.
    """
    if (req.envelope or "").strip().lower() != "stand_down":
        return False
    caller_reg = (req.caller_registration_id or "").strip()
    if not caller_reg:
        return False
    target_parent = _parent_thread_of(target_prov)
    if not target_parent:
        return False
    caller_prov = resolve_provenance(
        registration_id=caller_reg,
        host_listable=is_host_listable,
    )
    caller_parent = _parent_thread_of(caller_prov)
    return bool(caller_parent and caller_parent == target_parent)


def _authorized(req: PasteRequest, *, target_prov: dict[str, Any]) -> bool:
    grant = (req.grant or "").strip().lower()
    if grant in {"explicit", "operator", "hop-pair-grant"}:
        return True
    target_reg = str(target_prov.get("registration_id") or "").strip()
    if _watch_authorizes_predecessor_stand_down(
        req, target_reg=target_reg, target_prov=target_prov
    ):
        return True
    if _same_lane_authorizes_stand_down(req, target_prov=target_prov):
        return True
    caller_reg = (req.caller_registration_id or "").strip()
    superseded = (req.superseded_registration_id or "").strip()
    parent = (req.parent_thread or "").strip()
    if not caller_reg or not superseded or not parent:
        return False
    if superseded != target_reg:
        return False
    caller_prov = resolve_provenance(
        registration_id=caller_reg,
        host_listable=is_host_listable,
    )
    caller_parent = _parent_thread_of(caller_prov)
    target_parent = _parent_thread_of(target_prov)
    return bool(caller_parent and target_parent and caller_parent == target_parent == parent)


async def execute_paste(
    req: PasteRequest,
    store: ExecutionStore,
) -> PasteResponse | dict[str, Any]:
    """Paste into a named CSE with hop-pair or explicit grant authorization."""
    chat_url = (req.chat_url or "").strip() or None
    registration_id = (req.registration_id or "").strip() or None
    if not chat_url and not registration_id:
        return _protocol_error("identity_required", detail="paste requires chat_url or registration_id")

    target_prov = resolve_provenance(
        chat_url=chat_url,
        registration_id=registration_id,
        host_listable=is_host_listable,
    )
    if target_prov.get("state") == "conflict":
        emit(
            mcp_cse_session_conflict(
                reason=str(target_prov.get("reason") or "conflict"),
                registration_id=registration_id,
                chat_url=chat_url,
            )
        )
        return _protocol_error("ambiguous_identity", detail=str(target_prov.get("reason")))

    target_reg = str(target_prov.get("registration_id") or registration_id or "").strip()
    if not target_reg:
        return _protocol_error("not_attached")

    if self_supersession(req.caller_registration_id, target_reg):
        emit(
            mcp_cse_session_conflict(
                reason="self_supersession",
                registration_id=target_reg,
                chat_url=chat_url,
            )
        )
        return _protocol_error("self_supersession")

    if not _authorized(req, target_prov=target_prov):
        return _protocol_error(
            "paste_unauthorized",
            detail="cross-lane paste refused — require hop-pair or explicit grant",
        )

    key = _idempotency_key(req, target_registration_id=target_reg)
    cache_key = (target_reg, key)
    prior = _IDEMPOTENCY.get(cache_key)
    if prior is not None:
        replay = dict(prior)
        replay["replayed"] = True
        emit(
            mcp_cse_session_pasted(
                registration_id=target_reg,
                receipt=replay.get("receipt"),
                send_verified=bool(replay.get("send_verified")),
                replayed=True,
            )
        )
        return PasteResponse(**replay)

    if req.min_receipt == "human_visible":
        return PasteResponse(ok=False, error="human_visible_unsatisfiable", send_verified=False)

    followup_req = FollowupProjectAskRequest(
        chat_url=chat_url or target_prov.get("chat_url"),
        registration_id=target_reg,
        prompt_text=req.prompt_text,
        prompt_uri=req.prompt_uri,
        min_receipt=req.min_receipt,
    )
    result = await execute_followup(followup_req, store)
    if not result.ok:
        return PasteResponse(
            ok=False,
            error=result.error,
            detail=result.detail,
            code=result.error,
        )

    receipt = result.receipt
    send_verified = receipt_meets(receipt, req.min_receipt)
    ok = bool(result.ok and send_verified)
    response = PasteResponse(
        ok=ok,
        send_verified=send_verified,
        receipt=receipt,
        pasted_at=time.time(),
        streaming_at_paste=result.streaming_at_paste,
        target_binding=result.target_binding,
        idempotency_key=key,
        replayed=False,
        registration_id=result.registration_id or target_reg,
        chat_url=result.url or chat_url,
    )
    _IDEMPOTENCY[cache_key] = response.model_dump()
    emit(
        mcp_cse_session_pasted(
            registration_id=target_reg,
            receipt=receipt,
            send_verified=send_verified,
            replayed=False,
        )
    )
    return response
