"""Eligibility gate for live entity-state folding (W4 decision 2)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cortex_store.card_temporal import assertion_interval_contains, card_as_of_utc
from cortex_store.entity_state_flag_rule import (
    CONTRADICTION_PREFIXES,
    flag_fold_marker,
    flag_withholds,
)
from cortex_store.entity_state_provenance import (
    DENYLIST_FIELDS,
    claim_binds_field,
    derive_fold_value,
    parse_status_current,
    provenance_head,
)
_INFERENCE_DERIVATION = "inference"
_USER_STATEMENT = "user_statement"


@dataclass(frozen=True, slots=True)
class UtteranceInfo:
    """Resolvable operator utterance metadata (OSP rule b)."""

    operator_seat: bool


ResolveUtterance = Callable[[str], UtteranceInfo | None]


def _parse_evidence_uris(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text in ("[]", "null", "None"):
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [text] if text else []
        if isinstance(parsed, list):
            return [str(x) for x in parsed if str(x).strip()]
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    return []


def is_evidence_backed_w4(row: dict[str, Any]) -> bool:
    """W4 evidence gate (stricter than W2 ``_evidence_backed``)."""
    if row.get("confidence") != "confirmed":
        return False
    deriv = row.get("derivation_type")
    if deriv == _INFERENCE_DERIVATION or deriv is None:
        return False
    if deriv == _USER_STATEMENT:
        return False
    uris = _parse_evidence_uris(row.get("evidence_uris"))
    return bool(uris)


def is_osp_pointer(ref: str, resolver: ResolveUtterance | None) -> bool:
    """True when *ref* resolves to an operator utterance via injected resolver."""
    if resolver is None:
        return False
    info = resolver(ref)
    return info is not None and info.operator_seat


def is_osp_claim(row: dict[str, Any], resolver: ResolveUtterance | None) -> bool:
    if row.get("derivation_type") != _USER_STATEMENT:
        return False
    if row.get("review_status") == "flagged" and flag_withholds(
        "flagged", str(row.get("review_notes") or ""), row
    ):
        return False
    for ref in _parse_evidence_uris(row.get("evidence_uris")):
        if is_osp_pointer(ref, resolver):
            return True
    return False


def entity_is_concern(conn: sqlite3.Connection, entity_id: str, entity_type: str) -> bool:
    if entity_type == "concern":
        return True
    row = conn.execute(
        "SELECT 1 FROM concern_head WHERE concern_id = ? LIMIT 1",
        (entity_id,),
    ).fetchone()
    return row is not None


def claim_is_current(row: dict[str, Any], as_of: str | None = None) -> bool:
    if row.get("superseded_by") is not None:
        return False
    return assertion_interval_contains(
        row.get("valid_from"),
        row.get("valid_until"),
        as_of,
    )


def gate_refusal(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    field: str,
    row: dict[str, Any],
    state_provenance: dict[str, Any],
    resolver: ResolveUtterance | None,
    as_of: str | None = None,
    fail_closed_on_missing_head: bool = True,
) -> str | None:
    """Return refusal reason name, or None when the gate passes (not withhold)."""
    if field in DENYLIST_FIELDS:
        return "field_not_allowed"
    if entity_is_concern(conn, entity_id, entity_type):
        return "concern_refused"
    if row.get("superseded_by") is not None:
        return "superseded"
    if not assertion_interval_contains(
        row.get("valid_from"), row.get("valid_until"), as_of
    ):
        return "expired"
    if row.get("derivation_type") == _INFERENCE_DERIVATION:
        return "inference_refused"
    if row.get("confidence") != "confirmed":
        return "not_confirmed"
    if row.get("derivation_type") == _USER_STATEMENT and not is_evidence_backed_w4(row):
        if resolver is None and _parse_evidence_uris(row.get("evidence_uris")):
            return "osp_unresolvable"
    evidence = is_evidence_backed_w4(row)
    osp = is_osp_claim(row, resolver)
    if not evidence and not osp:
        return "not_evidence_backed"
    if row.get("derivation_type") == _USER_STATEMENT and not evidence and not osp:
        return "not_evidence_backed"
    field_records = state_provenance.get(field)
    records: list[dict[str, Any]] = (
        list(field_records) if isinstance(field_records, list) else []
    )
    head = provenance_head(records)
    claim_created = str(row.get("created_at") or "")
    claim_id = int(row["id"])
    if head is not None:
        if head.get("kind") == "fold":
            head_assertion = head.get("assertion_id")
            if head_assertion is not None:
                head_row = conn.execute(
                    "SELECT created_at, id FROM assertions WHERE id = ?",
                    (int(head_assertion),),
                ).fetchone()
                if head_row is None:
                    if fail_closed_on_missing_head:
                        return "head_claim_missing"
                    return None
                hc = str(head_row["created_at"])
                hid = int(head_row["id"])
                if int(head_assertion) != claim_id and (claim_created, claim_id) <= (
                    hc,
                    hid,
                ):
                    return "older_than_head"
            else:
                return "head_claim_missing"
        elif head.get("kind") == "operator_write":
            head_at = str(head.get("set_at") or "")
            if claim_created and head_at and claim_created <= head_at:
                return "older_than_head"
    for rec in records:
        if rec.get("kind") == "fold" and rec.get("assertion_id") == row.get("id"):
            revs = {r.get("reverses") for r in records if r.get("kind") == "reversal"}
            if rec.get("provenance_id") in revs:
                return "refold_after_reversal"
    return None


def __getattr__(name: str) -> object:
    if name in ("FieldState", "field_state"):
        from cortex_store.entity_state_read import FieldState, field_state

        return FieldState if name == "FieldState" else field_state
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CONTRADICTION_PREFIXES",
    "DENYLIST_FIELDS",
    "FieldState",
    "ResolveUtterance",
    "UtteranceInfo",
    "claim_binds_field",
    "claim_is_current",
    "derive_fold_value",
    "entity_is_concern",
    "field_state",
    "flag_fold_marker",
    "flag_withholds",
    "gate_refusal",
    "is_evidence_backed_w4",
    "is_osp_claim",
    "parse_status_current",
    "provenance_head",
]
