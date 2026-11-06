"""In-process owner API for Orion / shared endpoint wake claims (20995 ledger)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent_seat.registry import normalize_bus_address

from .connection import connect, write_connect
from .endpoint_wake_ledger import (
    record_orion_deferred_streaming,
    record_wake_result,
    resolve_orion_chat_wake,
)

_ORION_WAKE_HOUSE = "21207"
_RECEIPT_PREFIX = "paste_receipt:"
_DEFAULT_AGENT = "orion"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _encode_paste_receipt(
    *, outcome: str, reason_s: str, receipt: dict[str, Any]
) -> str:
    payload = {
        k: receipt[k]
        for k in (
            "ok",
            "delivered",
            "pasted",
            "inserted",
            "insert_attempted",
            "pasted_dom",
            "reason",
            "pointer_len",
            "conversation_id",
            "receipt",
            "phase",
            "uncertain_side_effect",
            "delivery_unconfirmed",
            "retryable",
            "clicked_unknown",
            "send_fallback_clicked",
            "error",
        )
        if k in receipt
    }
    payload["outcome"] = outcome
    payload["reason"] = reason_s
    return f"{_RECEIPT_PREFIX}{json.dumps(payload, sort_keys=True)}"


def _decode_paste_receipt(reason: str | None) -> dict[str, Any] | None:
    text = str(reason or "")
    if not text.startswith(_RECEIPT_PREFIX):
        return None
    try:
        parsed = json.loads(text[len(_RECEIPT_PREFIX) :])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def resolve_turn_id_for_wake(*, thread_id: str, turn_number: int) -> int | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM turns WHERE thread=? AND turn_number=? LIMIT 1",
            (thread_id, int(turn_number)),
        ).fetchone()
    return int(row["id"]) if row is not None else None


@dataclass(frozen=True)
class WakeClaimContext:
    """Dispatcher-held claim passed into orion-chat-wake (no re-claim in pipeline)."""

    wake_id: str
    agent: str
    thread_id: str
    turn_ids: tuple[int, ...]
    turn_numbers: tuple[int, ...]
    endpoint_url: str
    purpose: str = "pointer_wake"
    binding_reference: str | None = None
    attempt_id: str | None = None

    def normalized_agent(self) -> str:
        return normalize_bus_address(self.agent)

    def normalized_endpoint(self) -> str:
        return str(self.endpoint_url or "").split("?", 1)[0].strip()


def wake_claim_from_options(opts: dict[str, Any] | None) -> WakeClaimContext | None:
    if not opts:
        return None
    wake_id = str(opts.get("wake_id") or "").strip()
    thread_id = str(opts.get("thread") or "").strip()
    agent = str(opts.get("agent") or opts.get("wake_agent") or _DEFAULT_AGENT).strip()
    endpoint = str(
        opts.get("conversation_url") or opts.get("endpoint_url") or ""
    ).strip()
    raw_turn_ids = opts.get("turn_ids") or opts.get("covered_turn_ids") or []
    if not raw_turn_ids and opts.get("turn_id") is not None:
        raw_turn_ids = [opts.get("turn_id")]
    turn_ids: list[int] = []
    for item in raw_turn_ids if isinstance(raw_turn_ids, list) else [raw_turn_ids]:
        try:
            turn_ids.append(int(item))
        except (TypeError, ValueError):
            continue
    turn_numbers: list[int] = []
    raw_turn_numbers = opts.get("turn_numbers") or opts.get("turns")
    if raw_turn_numbers is None and opts.get("turn") is not None:
        raw_turn_numbers = [opts.get("turn")]
    if isinstance(raw_turn_numbers, str) and "," in raw_turn_numbers:
        raw_turn_numbers = [p.strip() for p in raw_turn_numbers.split(",") if p.strip()]
    for item in raw_turn_numbers or []:
        try:
            turn_numbers.append(int(item))
        except (TypeError, ValueError):
            continue
    if not thread_id or not turn_ids or not endpoint:
        return None
    if not wake_id:
        wake_id = uuid.uuid4().hex
    if not turn_numbers:
        turn_numbers = list(turn_ids)
    return WakeClaimContext(
        wake_id=wake_id,
        agent=agent,
        thread_id=thread_id,
        turn_ids=tuple(turn_ids),
        turn_numbers=tuple(turn_numbers),
        endpoint_url=endpoint.split("?", 1)[0],
        purpose=str(opts.get("purpose") or "pointer_wake"),
        binding_reference=(
            str(opts.get("binding_reference")).strip()
            if opts.get("binding_reference")
            else None
        ),
        attempt_id=str(opts.get("attempt_id")).strip()
        if opts.get("attempt_id")
        else None,
    )


def enrich_delivery_receipt(
    receipt: dict[str, Any], claim: WakeClaimContext
) -> dict[str, Any]:
    return {
        **receipt,
        "wake_id": claim.wake_id,
        "covered_turn_ids": list(claim.turn_ids),
        "turn_numbers": list(claim.turn_numbers),
        "agent": claim.normalized_agent(),
        "thread_id": claim.thread_id,
        "endpoint_url": claim.normalized_endpoint(),
        "purpose": claim.purpose,
        "binding_reference": claim.binding_reference,
        "attempt_id": claim.attempt_id,
    }


def _endpoint_from_actuation(actuation: dict[str, Any]) -> str:
    atype = str(actuation.get("type") or "")
    if atype in ("orion_chat", "chatgpt_work"):
        return str(actuation.get("conversation_url") or "").split("?", 1)[0].strip()
    if atype == "cursor_glass":
        return str(actuation.get("agent_id") or "").strip()
    return ""


class LedgerWakeDeliveryOwner:
    """Shared ledger owner: validate dispatcher claims, dedupe paste, record receipts."""

    def validate_claim_for_delivery(self, claim: WakeClaimContext) -> dict[str, Any]:
        if not str(claim.wake_id or "").strip():
            return {"ok": False, "reason": "wake_id_required"}
        if not claim.turn_ids:
            return {"ok": False, "reason": "turn_ids_required"}
        if not str(claim.endpoint_url or "").strip():
            return {"ok": False, "reason": "endpoint_required"}
        if not str(claim.thread_id or "").strip():
            return {"ok": False, "reason": "thread_id_required"}

        want_agent = claim.normalized_agent()
        want_endpoint = claim.normalized_endpoint()
        want_turns = tuple(sorted(int(t) for t in claim.turn_ids))

        with connect() as conn:
            rows = conn.execute(
                "SELECT agent, thread_id, turn_id, state FROM endpoint_wake_ledger "
                "WHERE wake_id=?",
                (claim.wake_id,),
            ).fetchall()

        claimed = [dict(r) for r in rows if str(r["state"]) == "claimed"]
        if not claimed:
            return {"ok": False, "reason": "wake_not_claimed"}

        ledger_turns = tuple(sorted(int(r["turn_id"]) for r in claimed))
        if ledger_turns != want_turns:
            return {"ok": False, "reason": "wake_claim_turn_mismatch"}

        ledger_thread = str(claimed[0]["thread_id"])
        if ledger_thread != str(claim.thread_id):
            return {"ok": False, "reason": "wake_claim_thread_mismatch"}

        for row in claimed:
            if normalize_bus_address(str(row["agent"])) != want_agent:
                return {"ok": False, "reason": "wake_claim_agent_mismatch"}

        binding = resolve_orion_chat_wake(
            claim.thread_id, agent=want_agent
        )
        if str(binding.get("state") or "") != "bound":
            return {"ok": False, "reason": "delivery_unbound"}

        actuation = dict((binding.get("current") or {}).get("actuation") or {})
        bound_endpoint = _endpoint_from_actuation(actuation)
        act_type = str(actuation.get("type") or "")
        if act_type == "chatgpt_work":
            if not bound_endpoint or want_endpoint != bound_endpoint:
                return {"ok": False, "reason": "wake_claim_endpoint_mismatch"}
        elif claim.thread_id == _ORION_WAKE_HOUSE:
            if bound_endpoint and want_endpoint != bound_endpoint:
                return {"ok": False, "reason": "wake_claim_endpoint_mismatch"}
        elif bound_endpoint and want_endpoint and want_endpoint != bound_endpoint:
            return {"ok": False, "reason": "wake_claim_endpoint_mismatch"}

        return {"ok": True, "reason": "claim_valid"}

    def _ledger_row(
        self, *, agent: str, thread_id: str, turn_id: int
    ) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM endpoint_wake_ledger WHERE agent=? AND thread_id=? "
                "AND turn_id=?",
                (agent, thread_id, int(turn_id)),
            ).fetchone()
        return dict(row) if row is not None else None

    def _duplicate_suppressed(self, row: dict[str, Any]) -> dict[str, Any]:
        first = _decode_paste_receipt(str(row.get("reason") or ""))
        if first is None:
            first = {
                "wake_id": row.get("wake_id"),
                "turn_id": row.get("turn_id"),
                "state": row.get("state"),
            }
        return {
            "ok": True,
            "reason": "duplicate_suppressed",
            "delivered": False,
            "pasted": False,
            "first_receipt": first,
        }

    def prepare_paste_delivery(self, claim: WakeClaimContext) -> dict[str, Any]:
        """Claim (thread, turn_id, endpoint) or refuse before paste."""
        agent = claim.normalized_agent()
        for turn_id in claim.turn_ids:
            row = self._ledger_row(
                agent=agent, thread_id=claim.thread_id, turn_id=turn_id
            )
            if row is not None and str(row.get("state") or "") == "delivered":
                return self._duplicate_suppressed(row)

        with connect() as conn:
            existing = conn.execute(
                "SELECT wake_id, state FROM endpoint_wake_ledger WHERE wake_id=?",
                (claim.wake_id,),
            ).fetchall()
        has_claimed = any(str(r["state"]) == "claimed" for r in existing)
        if has_claimed:
            gate = self.validate_claim_for_delivery(claim)
            if not gate.get("ok"):
                return {
                    "ok": False,
                    "reason": str(gate.get("reason") or "wake_claim_invalid"),
                    "pasted": False,
                    "delivered": False,
                }
            return {"ok": True, "claim": claim}

        acquired = self._acquire_direct_claim(claim)
        if not acquired.get("ok"):
            return acquired
        return {"ok": True, "claim": claim}

    def _acquire_direct_claim(self, claim: WakeClaimContext) -> dict[str, Any]:
        agent = claim.normalized_agent()
        stamp = _now()
        with write_connect() as conn:
            for turn_id, turn_number in zip(
                claim.turn_ids, claim.turn_numbers, strict=False
            ):
                row = conn.execute(
                    "SELECT state, wake_id, reason FROM endpoint_wake_ledger "
                    "WHERE agent=? AND thread_id=? AND turn_id=?",
                    (agent, claim.thread_id, int(turn_id)),
                ).fetchone()
                if row is not None:
                    state = str(row["state"])
                    if state == "delivered":
                        return self._duplicate_suppressed(dict(row))
                    if (
                        state == "claimed"
                        and str(row["wake_id"] or "") != claim.wake_id
                    ):
                        return {
                            "ok": False,
                            "reason": "paste_in_flight",
                            "pasted": False,
                            "delivered": False,
                        }
                conn.execute(
                    "INSERT OR IGNORE INTO endpoint_wake_ledger "
                    "(agent, thread_id, turn_id, turn_number, state, created_at) "
                    "VALUES (?,?,?,?, 'pending', ?)",
                    (agent, claim.thread_id, int(turn_id), int(turn_number), stamp),
                )
                cur = conn.execute(
                    "UPDATE endpoint_wake_ledger SET state='claimed', wake_id=?, "
                    "claimed_at=? WHERE agent=? AND thread_id=? AND turn_id=? "
                    "AND state IN ('pending') AND (wake_id IS NULL OR wake_id=?) "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM endpoint_wake_ledger d "
                    "  WHERE d.agent=? AND d.thread_id=? AND d.turn_id=? "
                    "  AND d.state='delivered'"
                    ")",
                    (
                        claim.wake_id,
                        stamp,
                        agent,
                        claim.thread_id,
                        int(turn_id),
                        claim.wake_id,
                        agent,
                        claim.thread_id,
                        int(turn_id),
                    ),
                )
                if int(cur.rowcount) != 1:
                    row2 = conn.execute(
                        "SELECT state, wake_id, reason FROM endpoint_wake_ledger "
                        "WHERE agent=? AND thread_id=? AND turn_id=?",
                        (agent, claim.thread_id, int(turn_id)),
                    ).fetchone()
                    if row2 is not None and str(row2["state"]) == "delivered":
                        return self._duplicate_suppressed(dict(row2))
                    return {
                        "ok": False,
                        "reason": "paste_in_flight",
                        "pasted": False,
                        "delivered": False,
                    }
        gate = self.validate_claim_for_delivery(claim)
        if not gate.get("ok"):
            return {
                "ok": False,
                "reason": str(gate.get("reason") or "wake_claim_invalid"),
                "pasted": False,
                "delivered": False,
            }
        return {"ok": True}

    def record_paste_receipt(
        self, claim: WakeClaimContext, receipt: dict[str, Any]
    ) -> None:
        from bus_watch.endpoint_wake_composer_defer import is_composer_defer_reason
        from bus_watch.orion_wake_receipt_map import (
            map_orion_receipt_to_outcomes,
            orion_bounded_defer_reason,
        )

        if str(receipt.get("reason") or "") == "duplicate_suppressed":
            return
        map_extra: dict[str, Any] = {}
        sub_id: int | None = None
        bind_ref = str(claim.binding_reference or "").strip()
        if bind_ref.startswith("agent_endpoint_subscriptions:"):
            try:
                sub_id = int(bind_ref.split(":", 1)[1])
            except (TypeError, ValueError):
                sub_id = None
        if sub_id is None:
            binding = resolve_orion_chat_wake(
                claim.thread_id, agent=claim.normalized_agent()
            )
            if str(binding.get("state") or "") == "bound":
                cur = dict(binding.get("current") or {})
                sid = cur.get("subscription_id")
                if sid is not None:
                    sub_id = int(sid)
        if receipt.get("delivered") is True:
            outcome = "delivered"
            reason = _encode_paste_receipt(
                outcome=outcome,
                reason_s=str(receipt.get("reason") or "delivered"),
                receipt=receipt,
            )
        elif str(receipt.get("reason") or "") == "deferred_streaming":
            # Why: live paste steers by default; deferral is for legacy ledger retries only.
            sched = record_orion_deferred_streaming(
                claim.wake_id,
                "exact",
                sub_id,
                None,
            )
            if sched.get("exhausted"):
                receipt.update(
                    {
                        "ok": False,
                        "reason": "deferred_exhausted",
                        "retry_eligible": False,
                    }
                )
            return
        elif str(receipt.get("reason") or "") == "deferred_exhausted":
            return
<<<<<<< HEAD
        elif receipt.get("insertion_unknown") is True:
            outcome = "unknown"
            reason = str(receipt.get("reason") or "insertion_unknown")
        elif receipt.get("pasted") and not receipt.get("delivered"):
            outcome = "unknown"
            reason_s = str(receipt.get("reason") or "delivery_unconfirmed")
            reason = _encode_paste_receipt(
                outcome=outcome, reason_s=reason_s, receipt=receipt
            )
        elif receipt.get("ok") is False:
            outcome = "refused"
            reason_s = str(receipt.get("reason") or "refused")
            if is_composer_defer_reason(reason_s):
                outcome = "pending"
            reason = _encode_paste_receipt(
                outcome=outcome, reason_s=reason_s, receipt=receipt
            )
=======
>>>>>>> d0b4200cd (fix(orion5944): unified receipt map, defer gate, cdp phase split)
        else:
            outcome, reason_s, map_extra = map_orion_receipt_to_outcomes(receipt)
            if outcome == "skipped":
                return
            reason = _encode_paste_receipt(
                outcome=outcome, reason_s=reason_s, receipt=receipt
            )
        detail_keys = (
            "receipt",
            "insert_attempted",
            "retryable",
            "clicked_unknown",
            "owned_draft_retained",
            "send_fallback_clicked",
            "phase",
            "uncertain_side_effect",
            "inserted",
            "pasted",
            "delivery_unconfirmed",
            "error",
        )
        wake_detail = {
            k: receipt[k]
            for k in detail_keys
            if k in receipt and receipt[k] is not None
        }
        wake_detail = {**wake_detail, **map_extra}
        defer_reason = str(
            receipt.get("composer_defer_reason") or receipt.get("reason") or ""
        )
        if outcome == "pending" and (
            is_composer_defer_reason(defer_reason)
            or orion_bounded_defer_reason(defer_reason)
            or defer_reason == "cdp_transport_error"
        ):
            wake_detail["release_claim_for_retry"] = True
            wake_detail["composer_defer_reason"] = reason_s
        record_wake_result(
            claim.wake_id,
            outcome,
            reason,
            "exact",
            sub_id,
            None,
            wake_detail=wake_detail or None,
        )


def shared_wake_delivery_owner() -> LedgerWakeDeliveryOwner:
    """Factory for agent_bus_store-mounted processes (Orion hop seam)."""
    return LedgerWakeDeliveryOwner()


__all__ = [
    "LedgerWakeDeliveryOwner",
    "WakeClaimContext",
    "enrich_delivery_receipt",
    "resolve_turn_id_for_wake",
    "shared_wake_delivery_owner",
    "wake_claim_from_options",
]
