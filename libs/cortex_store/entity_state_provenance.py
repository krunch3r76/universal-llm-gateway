"""Provenance records, predicate binding, and attribute parsing (W4)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from dataclasses import dataclass
from typing import Any, Final, Literal

from predicate_form.registry import is_status_functor

DENYLIST_FIELDS: Final[frozenset[str]] = frozenset(
    {"description", "predicate_form", "state_provenance", "name", "outcome", "urgency"}
)
FOLD_FIELDS: Final[frozenset[str]] = frozenset({"application_submitted", "workflow_state"})
FoldOutcome = Literal["folded", "reversed", "withheld", "noop", "refused"]


@dataclass(frozen=True, slots=True)
class FoldResult:
    outcome: FoldOutcome
    reason: str | None = None
    marker: str | None = None
    flag_detail: dict[str, Any] | None = None
    field: str | None = None
    provenance_id: str | None = None
    previous_value: object | None = None
    new_value: object | None = None
    events_emitted: int = 0
    emit_error: str | None = None


def parse_attributes(raw: object) -> dict[str, Any]:
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


def records_for_field(attrs: dict[str, Any], field: str) -> list[dict[str, Any]]:
    sp = attrs.get("state_provenance")
    if not isinstance(sp, dict):
        return []
    recs = sp.get(field)
    if not isinstance(recs, list):
        return []
    return [dict(r) for r in recs if isinstance(r, dict)]


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
    del entity_id, predicate_form  # binding is token-driven for allowlisted fields
    if field == "application_submitted":
        return token == "submitted"
    if field == "workflow_state":
        return True
    return False


def encode_fold_event_value(value: object) -> str:
    """Single encoding for fold/reverse event payloads."""
    if value is None:
        return "null"
    if isinstance(value, str):
        return value
    return json.dumps(value)


def values_match_strict(current: object | None, head_val: object | None) -> bool:
    """Type-strict equality for verified field checks (decision 13)."""
    if current is None and head_val is None:
        return True
    if type(current) is not type(head_val):
        return False
    return current == head_val


def derive_fold_value(field: str, token: str) -> object | None:
    if field == "application_submitted":
        return True if token == "submitted" else None
    if field == "workflow_state":
        return token
    return None


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def attr_value_present(attrs: dict[str, Any], field: str) -> tuple[object | None, bool]:
    if field not in attrs:
        return None, False
    return attrs.get(field), True


def fold_field_updates(
    field: str,
    attrs: dict[str, Any],
    state_provenance: dict[str, Any],
    field_records: list[dict[str, Any]],
    value: object,
) -> dict[str, object]:
    new_sp = {**state_provenance, field: field_records}
    new_attrs = {**attrs, "state_provenance": new_sp}
    if field == "workflow_state":
        return {"workflow_state": value, "attributes": new_attrs}
    new_attrs[field] = value
    return {"attributes": new_attrs}


def state_sha256(conn: sqlite3.Connection, entity_id: str) -> str:
    row = conn.execute(
        "SELECT attributes, workflow_state FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if row is None:
        return hashlib.sha256(b"{}").hexdigest()
    attrs = parse_attributes(row["attributes"])
    payload = json.dumps(
        {"attributes": attrs, "workflow_state": row["workflow_state"]},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()
