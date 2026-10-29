"""In-process owner API for Orion / shared endpoint wake claims (20995 ledger)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent_seat.registry import normalize_bus_address

from .connection import connect, write_connect
from .endpoint_wake_ledger import record_wake_result, resolve_wake_delivery_binding

_ORION_WAKE_HOUSE = "21207"
_RECEIPT_PREFIX = "paste_receipt:"
_DEFAULT_AGENT = "orion"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _encode_paste_receipt(receipt: dict[str, Any]) -> str:
    payload = {
        k: receipt[k]
        for k in ("ok", "delivered", "pasted", "reason", "pointer_len", "conversation_id")
        if k in receipt
    }
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
    endpoint = str(opts.get("conversation_url") or opts.get("endpoint_url") or "").strip()
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
        attempt_id=str(opts.get("attempt_id")).strip() if opts.get("attempt_id") else None,
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
    if atype == "orion_chat":
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

        binding = resolve_wake_delivery_binding(house_id=claim.thread_id)
        if str(binding.get("state") or "") != "bound":
            return {"ok": False, "reason": "delivery_unbound"}

        actuation = dict((binding.get("current") or {}).get("actuation") or {})
        bound_endpoint = _endpoint_from_actuation(actuation)
        if claim.thread_id == _ORION_WAKE_HOUSE:
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
            row = self._ledger_row(agent=agent, thread_id=claim.thread_id, turn_id=turn_id)
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
                    if state == "claimed" and str(row["wake_id"] or "") != claim.wake_id:
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
        if str(receipt.get("reason") or "") == "duplicate_suppressed":
            return
        if receipt.get("delivered") is True:
            outcome = "delivered"
            reason = _encode_paste_receipt(receipt)
        elif str(receipt.get("reason") or "") == "deferred_streaming":
            outcome = "pending"
            reason = "deferred_streaming"
        elif receipt.get("pasted") and not receipt.get("delivered"):
            outcome = "unknown"
            reason = str(receipt.get("reason") or "delivery_unconfirmed")
        elif receipt.get("ok") is False:
            outcome = "refused"
            reason = str(receipt.get("reason") or "refused")
        else:
            outcome = "unknown"
            reason = str(receipt.get("reason") or "unnamed")
        record_wake_result(
            claim.wake_id,
            outcome,
            reason,
            "exact",
            None,
            None,
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
