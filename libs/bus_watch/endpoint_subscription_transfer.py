"""S3 transfer verification and predecessor retirement after hop completion."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bus_watch.endpoint_subscription_actuation import (
    build_request_subject,
    load_hop_request_meta,
    parse_hop_request_uuid,
)
from bus_watch.endpoint_subscription_join import (
    RegistryClaims,
    claim_tab_uuids,
    h1_rows_for_scope,
    is_uuid_agent_id,
    live_claims_for_scope,
)
from bus_watch.fable_lock import WATCH_DIR
from bus_watch.ide_budget_exclude import pin_retired_resume_transcript
from bus_watch.ide_hop_retire import suffix_retired_tab_title

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransferContext:
    scope: str
    mailbox_agent: str
    old_uuid: str
    new_uuid: str
    request_row_id: int
    request_turn_number: int
    rows: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    ledger_rows: list[dict[str, Any]]


def _row_for_mailbox(
    rows: list[dict[str, Any]], scope: str, mailbox_agent: str
) -> dict[str, Any] | None:
    matches = [
        row
        for row in h1_rows_for_scope(rows, scope)
        if str(row.get("agent") or "").strip() == mailbox_agent
    ]
    if not matches:
        return None
    return max(matches, key=lambda r: int(r.get("id") or 0))


def _ledger_row_tab_uuid(row: dict[str, Any]) -> str | None:
    """Tab UUID: live ``delivery_agent_id``, then legacy ``agent_id``, then uuid ``agent``."""
    uid = is_uuid_agent_id(row.get("delivery_agent_id"))
    if uid:
        return uid
    uid = is_uuid_agent_id(row.get("agent_id"))
    if uid:
        return uid
    return is_uuid_agent_id(row.get("agent"))


def _ledger_delivery_sort_key(row: dict[str, Any]) -> tuple[int, str, int]:
    for field in ("delivered_at", "finished_at", "created_at"):
        stamp = str(row.get(field) or "").strip()
        if stamp:
            return (0, stamp, int(row.get("id") or 0))
    return (1, "", int(row.get("id") or 0))


def _ack_from_successor(turn: dict[str, Any], successor_uuid: str) -> bool:
    subject = str(turn.get("subject") or "")
    if not subject.upper().startswith("ACK|"):
        return False
    from_id = str(
        turn.get("from_agent_id") or turn.get("from_agent") or ""
    ).lower()
    succ = successor_uuid.lower()
    return succ in from_id or from_id.endswith(succ)


def is_transfer_complete(ctx: TransferContext) -> bool:
    t1 = False
    current = _row_for_mailbox(ctx.rows, ctx.scope, ctx.mailbox_agent)
    if current is not None:
        current_id = int(current.get("id") or 0)
        current_uuid = is_uuid_agent_id(current.get("agent_id"))
        if (
            current_uuid == ctx.new_uuid.lower()
            and current_id > ctx.request_row_id
        ):
            t1 = True

    claim_uuids = claim_tab_uuids(ctx.claims)
    t2 = (
        ctx.new_uuid.lower() in claim_uuids
        and ctx.old_uuid.lower() not in claim_uuids
    )

    t3 = False
    for turn in ctx.turns:
        turn_no = int(turn.get("turn_number") or 0)
        if turn_no <= ctx.request_turn_number:
            continue
        if _ack_from_successor(turn, ctx.new_uuid):
            t3 = True
            break

    t4 = True
    open_states = frozenset({"pending", "deferred"})
    old_l = ctx.old_uuid.lower()
    new_l = ctx.new_uuid.lower()
    for row in ctx.ledger_rows:
        state = str(row.get("state") or "")
        if state in open_states:
            row_uuid = _ledger_row_tab_uuid(row)
            if row_uuid == old_l:
                t4 = False
                break

    delivered = [
        row
        for row in ctx.ledger_rows
        if str(row.get("state") or "") == "delivered"
    ]
    if delivered and t4:
        ordered = sorted(delivered, key=_ledger_delivery_sort_key)
        first_uuid = _ledger_row_tab_uuid(ordered[0])
        if first_uuid and first_uuid != new_l:
            t4 = False

    return t1 and t2 and t3 and t4


def execute_transfer_retirement(
    *,
    scope: str,
    old_uuid: str,
    new_uuid: str,
    old_title: str,
    emit_transfer: Callable[..., None],
    rename_tab: Callable[[str, str], dict[str, Any]] | None = None,
    watch_dir: Path = WATCH_DIR,
) -> dict[str, Any]:
    new_title = suffix_retired_tab_title(old_title)
    if rename_tab is None:
        return {
            "ok": False,
            "reason": "rename_unavailable",
            "emitted": False,
            "old_uuid": old_uuid,
            "new_uuid": new_uuid,
        }
    rename_result = rename_tab(old_uuid, new_title)
    if not rename_result.get("ok"):
        return {
            "ok": False,
            "reason": rename_result.get("reason") or "rename_failed",
            "emitted": False,
            "rename": rename_result,
            "old_uuid": old_uuid,
            "new_uuid": new_uuid,
        }
    pin = pin_retired_resume_transcript(scope, old_uuid, watch_dir=watch_dir)
    emit_transfer(scope=scope, departing_uuid=old_uuid, successor_uuid=new_uuid)
    return {
        "ok": True,
        "emitted": True,
        "rename": rename_result,
        "pin": pin,
        "old_uuid": old_uuid,
        "new_uuid": new_uuid,
    }


def _find_hop_request_turn(turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    subject = build_request_subject()
    matches = [
        t
        for t in turns
        if str(t.get("subject") or "").strip() == subject
    ]
    if not matches:
        return None
    return max(matches, key=lambda t: int(t.get("turn_number") or 0))


def _predecessor_row(
    rows: list[dict[str, Any]], scope: str, old_uuid: str
) -> dict[str, Any] | None:
    for row in h1_rows_for_scope(rows, scope):
        if is_uuid_agent_id(row.get("agent_id")) == old_uuid.lower():
            return row
    return None


def maybe_retire_predecessor(
    store: Any,
    *,
    root_id: str,
    registry: RegistryClaims,
    all_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Retire departing tab U after hop when T1–T4 hold (claim already on V)."""
    try:
        turns_payload = store.turns(root_id, last=50)
        turns = list(turns_payload.get("turns") or [])
    except Exception:
        return None

    request_turn = _find_hop_request_turn(turns)
    if request_turn is None:
        return None

    body = str(request_turn.get("body") or "")
    old_uuid = parse_hop_request_uuid(body)
    if not old_uuid:
        return None

    claims = live_claims_for_scope(registry, root_id)
    claim_uuids = claim_tab_uuids(claims)
    if len(claim_uuids) != 1:
        return None
    new_uuid = next(iter(claim_uuids))
    if new_uuid == old_uuid:
        return None

    meta = load_hop_request_meta(root_id, old_uuid, "hop")
    if not meta:
        meta = load_hop_request_meta(root_id, old_uuid, "emergency")
    request_row_id = int(meta.get("subscription_id") or 0)
    if not request_row_id:
        pred_row = _predecessor_row(all_rows, root_id, old_uuid)
        request_row_id = int(pred_row.get("id") or 0) if pred_row else 0

    mailbox = str(
        meta.get("mailbox_agent")
        or (request_turn.get("to_agent") or "")
    ).strip()

    try:
        ledger = store._bus(
            "GET",
            "/endpoint-wake/ledger",
            params={"thread": root_id, "limit": 100},
        )
        ledger_rows = list((ledger or {}).get("rows") or [])
    except Exception:
        logger.exception("endpoint_subscription_transfer: ledger GET failed")
        return {"ok": False, "reason": "ledger_unavailable"}

    ctx = TransferContext(
        scope=root_id,
        mailbox_agent=mailbox,
        old_uuid=old_uuid,
        new_uuid=new_uuid,
        request_row_id=request_row_id,
        request_turn_number=int(request_turn.get("turn_number") or 0),
        rows=all_rows,
        claims=claims,
        turns=turns,
        ledger_rows=ledger_rows,
    )
    if not is_transfer_complete(ctx):
        return {"ok": False, "reason": "transfer_incomplete"}

    pred_row = _predecessor_row(all_rows, root_id, old_uuid)
    old_title = str((pred_row or {}).get("title") or old_uuid)

    def _emit(**kw: Any) -> None:
        from bus_watch.events import emit_ide_hop_transfer_accepted

        emit_ide_hop_transfer_accepted(**kw)

    try:
        from cursor_bridge.cursor_agents_archive import rename_agent_row_in_sidebar
    except ImportError:
        logger.warning("endpoint_subscription_transfer: rename import unavailable")
        return {"ok": False, "reason": "rename_unavailable"}

    return execute_transfer_retirement(
        scope=root_id,
        old_uuid=old_uuid,
        new_uuid=new_uuid,
        old_title=old_title,
        emit_transfer=_emit,
        rename_tab=rename_agent_row_in_sidebar,
    )


__all__ = [
    "TransferContext",
    "execute_transfer_retirement",
    "is_transfer_complete",
    "maybe_retire_predecessor",
]
