"""Eligibility and flag-rule gates for live entity-state folding (W4).

Encodes decision 2 (eligibility) and decision 5 (flag withhold vs fold-with-marker)
from ``spec:cortex-live-entity-state-w4-fold-path``. Kept separate from
``entity_state_fold`` so the fold transaction module stays within modularize SLOC.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Literal

from predicate_form.registry import is_status_functor

from cortex_store.card import _extract_flag_reason
from cortex_store.card_temporal import (
    assertion_interval_contains,
    card_as_of_utc,
    install_card_temporal_function,
)
from cortex_store.db import query as db_query

CONTRADICTION_PREFIXES: Final[tuple[str, ...]] = (
    "contradiction:",
    "Cross-entity contradiction detected:",
    "Semantic contradiction:",
)

DENYLIST_FIELDS: Final[frozenset[str]] = frozenset(
    {"description", "predicate_form", "state_provenance", "name", "outcome", "urgency"}
)

_INFERENCE_DERIVATION = "inference"
_USER_STATEMENT = "user_statement"


@dataclass(frozen=True, slots=True)
class UtteranceInfo:
    """Resolvable operator utterance metadata (OSP rule b)."""

    operator_seat: bool


ResolveUtterance = Callable[[str], UtteranceInfo | None]


def _chunks(notes: str | None) -> list[str]:
    if not notes:
        return []
    return [part.strip() for part in notes.split(";")]


def _chunk_has_contradiction_prefix(chunk: str) -> bool:
    lower = chunk.lower()
    for prefix in CONTRADICTION_PREFIXES:
        if lower.startswith(prefix.lower()):
            return True
    return False


def flag_withholds(review_status: str | None, review_notes: str | None, row: dict[str, Any]) -> bool:
    """True when decision 5 withholds authority (contradiction chunk or reviewed notes)."""
    if review_status != "flagged":
        return False
    notes_raw = str(review_notes or "")
    stripped = notes_raw.strip()
    for chunk in _chunks(notes_raw):
        if _chunk_has_contradiction_prefix(chunk):
            return True
    if not stripped:
        return False
    for chunk in _chunks(notes_raw):
        if chunk.startswith("predicate normalize:"):
            return False
    reviewer = row.get("reviewer")
    reviewed_at = row.get("reviewed_at")
    if reviewer is not None or reviewed_at is not None:
        return True
    return False


def flag_fold_marker(
    review_status: str | None, review_notes: str | None
) -> tuple[str | None, dict[str, Any] | None]:
    """Marker and flag_detail for foldable flagged claims (decision 5)."""
    if review_status != "flagged":
        return None, None
    notes_raw = str(review_notes or "")
    token = _extract_flag_reason(notes_raw)
    return "flagged, no reason", {"notes": review_notes, "token": token}


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
    # Ordering vs head (decision 2)
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
    # Reversal ineligibility
    for rec in records:
        if rec.get("kind") == "fold" and rec.get("assertion_id") == row.get("id"):
            revs = {r.get("reverses") for r in records if r.get("kind") == "reversal"}
            if rec.get("provenance_id") in revs:
                return "refold_after_reversal"
    return None


def provenance_head(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Last non-reversed fold or operator_write record (decision 8)."""
    reversed_ids = {
        r.get("reverses") for r in records if r.get("kind") == "reversal" and r.get("reverses")
    }
    head: dict[str, Any] | None = None
    for rec in records:
        if rec.get("kind") not in ("fold", "operator_write"):
            continue
        pid = rec.get("provenance_id")
        if pid in reversed_ids:
            continue
        if rec.get("kind") == "operator_write" and not rec.get("provenance_id"):
            continue
        head = rec
    return head


def parse_status_current(predicate_form: str) -> tuple[str, str] | None:
    """Parse ``status(entity, token, current)`` into entity id and state token."""
    from predicate_form.parser import parse as parse_predicate

    try:
        pred = parse_predicate(predicate_form)
    except Exception:
        return None
    if not is_status_functor(pred.name) or len(pred.args) < 3:
        return None
    if pred.args[-1].lower() != "current":
        return None
    return pred.args[0], pred.args[1]


def claim_binds_field(field: str, entity_id: str, predicate_form: str, token: str) -> bool:
    """True when the claim predicate matches the field's fold rule (decision 3–4)."""
    if field == "application_submitted":
        return token == "submitted"
    if field == "workflow_state":
        return True
    return False


def derive_fold_value(field: str, token: str) -> object | None:
    if field == "application_submitted":
        return True if token == "submitted" else None
    if field == "workflow_state":
        return token
    return None


@dataclass(frozen=True, slots=True)
class FieldState:
    """Read-rule projection for one structured field (decision 13)."""

    value: object | None
    present: bool
    verification: Literal["verified", "unverified"]
    head_ref: str | None
    newer_claim_id: int | None
    marker: str | None = None


def _parse_attrs(raw: object) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _records_for_field(attrs: dict[str, Any], field: str) -> list[dict[str, Any]]:
    sp = attrs.get("state_provenance")
    if not isinstance(sp, dict):
        return []
    recs = sp.get(field)
    if not isinstance(recs, list):
        return []
    return [dict(r) for r in recs if isinstance(r, dict)]


def _field_verified(
    attrs: dict[str, Any], field: str, workflow_state: str | None
) -> tuple[Literal["verified", "unverified"], dict[str, Any] | None]:
    records = _records_for_field(attrs, field)
    ids = [r.get("provenance_id") for r in records if r.get("provenance_id")]
    if len(ids) != len(set(ids)):
        return "unverified", None
    head = provenance_head(records)
    if head is None:
        return "unverified", None
    if not head.get("ref") or not head.get("ref_kind"):
        return "unverified", head
    current = workflow_state if field == "workflow_state" else attrs.get(field)
    head_val = head.get("value")
    if not (current is None and head_val is None) and current != head_val:
        return "unverified", head
    return "verified", head


def _fetch_claim_rows(conn: sqlite3.Connection, entity_id: str) -> list[dict[str, Any]]:
    rows = db_query(
        conn,
        "SELECT * FROM assertions WHERE entity_id = ? ORDER BY created_at DESC, id DESC",
        (entity_id,),
    )
    return [dict(r) for r in rows]


def _claim_eligible_for_field_state(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    field: str,
    row: dict[str, Any],
    state_provenance: dict[str, Any],
    resolver: ResolveUtterance | None,
) -> bool:
    if flag_withholds(row.get("review_status"), row.get("review_notes"), row):
        return False
    pf = str(row.get("predicate_form") or "")
    parsed = parse_status_current(pf)
    if parsed is None:
        return False
    bound_entity, token = parsed
    if bound_entity != entity_id or str(row.get("entity_id")) != entity_id:
        return False
    if not claim_binds_field(field, entity_id, pf, token):
        return False
    if gate_refusal(
        conn,
        entity_id=entity_id,
        entity_type=entity_type,
        field=field,
        row=row,
        state_provenance=state_provenance,
        resolver=resolver,
        as_of=card_as_of_utc(),
        fail_closed_on_missing_head=False,
    ):
        return False
    records = state_provenance.get(field)
    if isinstance(records, list):
        revs = {r.get("reverses") for r in records if r.get("kind") == "reversal"}
        for rec in records:
            if rec.get("kind") == "fold" and rec.get("assertion_id") == row.get("id"):
                if rec.get("provenance_id") in revs:
                    return False
    return True


def field_state(
    conn: sqlite3.Connection,
    entity_id: str,
    field: str,
    *,
    resolver: ResolveUtterance | None = None,
) -> FieldState:
    """Read-only field head + newest eligible claim (decision 13)."""
    install_card_temporal_function(conn)
    ent = conn.execute(
        "SELECT type, attributes, workflow_state FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if ent is None:
        return FieldState(None, False, "unverified", None, None)
    attrs = _parse_attrs(ent["attributes"])
    sp = attrs.get("state_provenance")
    state_provenance = dict(sp) if isinstance(sp, dict) else {}
    verification, head = _field_verified(attrs, field, ent["workflow_state"])
    if (
        verification == "verified"
        and head
        and head.get("ref_kind") == "assertion"
        and head.get("ref")
    ):
        ref = str(head["ref"])
        if ref.startswith("assertion:"):
            try:
                head_aid = int(ref.split(":", 1)[1])
            except ValueError:
                verification = "unverified"
            else:
                if (
                    conn.execute(
                        "SELECT 1 FROM assertions WHERE id = ?", (head_aid,)
                    ).fetchone()
                    is None
                ):
                    verification = "unverified"
    head_ref = str(head.get("ref")) if head and head.get("ref") else None
    marker = str(head.get("marker")) if head and head.get("marker") else None
    if field == "workflow_state":
        value, present = ent["workflow_state"], ent["workflow_state"] is not None
    else:
        val = attrs.get(field)
        present = field in attrs and val is not None
        value = val
    newest_id: int | None = None
    for row in _fetch_claim_rows(conn, entity_id):
        if _claim_eligible_for_field_state(
            conn,
            entity_id=entity_id,
            entity_type=str(ent["type"]),
            field=field,
            row=row,
            state_provenance=state_provenance,
            resolver=resolver,
        ):
            newest_id = int(row["id"])
            break
    newer_out: int | None = None
    if verification == "unverified":
        newer_out = newest_id
    elif newest_id is not None and head and head.get("assertion_id") != newest_id:
        newer_out = newest_id
    return FieldState(
        value=value,
        present=present,
        verification=verification,
        head_ref=head_ref,
        newer_claim_id=newer_out,
        marker=marker,
    )
