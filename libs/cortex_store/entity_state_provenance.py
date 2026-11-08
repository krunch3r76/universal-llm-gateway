"""Provenance records, predicate binding, and attribute parsing (W4)."""

from __future__ import annotations

import json
from typing import Any, Final

from predicate_form.registry import is_status_functor

DENYLIST_FIELDS: Final[frozenset[str]] = frozenset(
    {"description", "predicate_form", "state_provenance", "name", "outcome", "urgency"}
)


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


def derive_fold_value(field: str, token: str) -> object | None:
    if field == "application_submitted":
        return True if token == "submitted" else None
    if field == "workflow_state":
        return token
    return None
