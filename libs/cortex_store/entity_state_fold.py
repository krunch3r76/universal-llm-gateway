"""Live entity-state fold path (W4): evidence-gated claim → structured field."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal

from fastapi import HTTPException

from cortex_store.card_temporal import card_as_of_utc, install_card_temporal_function
from cortex_store.db import decode_row
from cortex_store.db import query as db_query
from cortex_store.entity_crud import ENTITY_JSON_FIELDS, update_entity_impl
from cortex_store.entity_state_fold_gate import (
    DENYLIST_FIELDS,
    FieldState,
    ResolveUtterance,
    claim_binds_field,
    derive_fold_value,
    field_state,
    flag_fold_marker,
    flag_withholds,
    gate_refusal,
    parse_status_current,
    provenance_head,
)
from cortex_store.event_publisher import cortex_entity_state_folded

__all__ = [
    "FieldState",
    "FoldResult",
    "field_state",
    "fold_claim",
    "reverse_fold",
    "state_sha256",
]

FOLD_FIELDS: Final[frozenset[str]] = frozenset({"application_submitted", "workflow_state"})

Outcome = Literal["folded", "reversed", "withheld", "noop", "refused"]


@dataclass(frozen=True, slots=True)
class FoldResult:
    """Result of ``fold_claim`` or ``reverse_fold``."""

    outcome: Outcome
    reason: str | None = None
    marker: str | None = None
    flag_detail: dict[str, Any] | None = None
    field: str | None = None
    provenance_id: str | None = None
    previous_value: object | None = None
    new_value: object | None = None
    events_emitted: int = 0
    emit_error: str | None = None


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_attributes(raw: object) -> dict[str, Any]:
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


def state_sha256(conn: sqlite3.Connection, entity_id: str) -> str:
    """Sha256 of canonical JSON for attributes + workflow_state (decision 7)."""
    row = conn.execute(
        "SELECT attributes, workflow_state FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if row is None:
        return hashlib.sha256(b"{}").hexdigest()
    attrs = _parse_attributes(row["attributes"])
    payload = json.dumps(
        {"attributes": attrs, "workflow_state": row["workflow_state"]},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _attr_value_present(attrs: dict[str, Any], field: str) -> tuple[object | None, bool]:
    if field not in attrs and field != "workflow_state":
        return None, False
    if field == "workflow_state":
        return None, False
    val = attrs.get(field)
    return val, True


def _conn_precheck(conn: sqlite3.Connection) -> str | None:
    if getattr(conn, "in_transaction", False):
        return "conn_in_transaction"
    autocommit = getattr(conn, "autocommit", None)
    if autocommit is True:
        return "conn_isolation_unsupported"
    return None


def _read_entity(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any] | None:
    rows = db_query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    if not rows:
        return None
    return decode_row(rows[0], ENTITY_JSON_FIELDS)


def _read_assertion(conn: sqlite3.Connection, assertion_id: int) -> dict[str, Any] | None:
    rows = db_query(conn, "SELECT * FROM assertions WHERE id = ?", (assertion_id,))
    if not rows:
        return None
    return dict(rows[0])


def _refused(reason: str, field: str | None = None) -> FoldResult:
    return FoldResult(outcome="refused", reason=reason, field=field)


def fold_claim(
    conn: sqlite3.Connection,
    entity_id: str,
    assertion_id: int | None,
    field: str,
    *,
    expected_state_sha256: str | None = None,
    resolver: ResolveUtterance | None = None,
) -> FoldResult:
    """Fold an eligible claim onto a structured entity field (W4 write path)."""
    if assertion_id is None:
        return _refused("assertion_id_required", field)
    if field in DENYLIST_FIELDS:
        return _refused("field_not_allowed", field)
    if field not in FOLD_FIELDS:
        return _refused("field_not_allowed", field)
    pre = _conn_precheck(conn)
    if pre:
        return _refused(pre, field)
    install_card_temporal_function(conn)
    saved_iso = conn.isolation_level
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        entity = _read_entity(conn, entity_id)
        if entity is None:
            conn.rollback()
            return _refused("stale_entity", field)
        claim = _read_assertion(conn, assertion_id)
        if claim is None:
            conn.rollback()
            return _refused("superseded", field)
        if expected_state_sha256 is not None:
            current_hash = state_sha256(conn, entity_id)
            if current_hash != expected_state_sha256:
                conn.rollback()
                return _refused("stale_entity", field)
        attrs = _parse_attributes(entity.get("attributes"))
        sp_raw = attrs.get("state_provenance")
        state_provenance: dict[str, Any] = dict(sp_raw) if isinstance(sp_raw, dict) else {}
        entity_type = str(entity.get("type") or "")
        if flag_withholds(claim.get("review_status"), claim.get("review_notes"), claim):
            conn.rollback()
            notes = str(claim.get("review_notes") or "")
            return FoldResult(
                outcome="withheld",
                reason=notes,
                field=field,
            )
        pf = str(claim.get("predicate_form") or "")
        parsed = parse_status_current(pf)
        if parsed is None:
            conn.rollback()
            return _refused("predicate_mismatch", field)
        bound_entity, token = parsed
        if bound_entity != entity_id or str(claim.get("entity_id")) != entity_id:
            conn.rollback()
            return _refused("claim_entity_mismatch", field)
        if not claim_binds_field(field, entity_id, pf, token):
            conn.rollback()
            return _refused("predicate_mismatch", field)
        new_value = derive_fold_value(field, token)
        if new_value is None:
            conn.rollback()
            return _refused("predicate_mismatch", field)
        refusal = gate_refusal(
            conn,
            entity_id=entity_id,
            entity_type=entity_type,
            field=field,
            row=claim,
            state_provenance=state_provenance,
            resolver=resolver,
            as_of=card_as_of_utc(),
            fail_closed_on_missing_head=True,
        )
        if refusal:
            conn.rollback()
            return _refused(refusal, field)
        marker, flag_detail = flag_fold_marker(
            claim.get("review_status"), claim.get("review_notes")
        )
        records = list(state_provenance.get(field) or [])
        head = provenance_head(records)
        if (
            head is not None
            and head.get("kind") == "fold"
            and head.get("assertion_id") == assertion_id
            and head.get("value") == new_value
        ):
            conn.rollback()
            return FoldResult(outcome="noop", field=field)
        if field == "workflow_state":
            prev_val = entity.get("workflow_state")
            prev_present = prev_val is not None
        else:
            prev_val, prev_present = _attr_value_present(attrs, field)
        prov_id = f"{field}:{len(records) + 1}"
        fold_record: dict[str, Any] = {
            "provenance_id": prov_id,
            "kind": "fold",
            "ref": f"assertion:{assertion_id}",
            "ref_kind": "assertion",
            "value": new_value,
            "set_by": "fold",
            "set_at": _now_iso(),
            "assertion_id": assertion_id,
            "previous_value": prev_val,
            "previous_present": prev_present,
            "marker": marker,
            "flag_detail": flag_detail,
        }
        new_records = [*records, fold_record]
        new_sp = {**state_provenance, field: new_records}
        new_attrs = {**attrs, "state_provenance": new_sp}
        if field == "workflow_state":
            new_attrs.pop("workflow_state", None)
            updates: dict[str, object] = {
                "workflow_state": new_value,
                "attributes": new_attrs,
            }
        else:
            new_attrs[field] = new_value
            updates = {"attributes": new_attrs}
        post_emits: list[Callable[[], None]] = []

        def _emit_fold() -> None:
            cortex_entity_state_folded(
                entity_id=entity_id,
                field=field,
                provenance_id=prov_id,
                assertion_id=assertion_id,
                value=json.dumps(new_value) if not isinstance(new_value, str) else new_value,
            )

        post_emits.append(_emit_fold)
        try:
            update_entity_impl(
                conn,
                entity_id=entity_id,
                updates=updates,
                commit=False,
                post_commit_emits=post_emits,
            )
        except HTTPException:
            conn.rollback()
            return FoldResult(outcome="refused", reason="gate_refused", field=field)
        conn.commit()
        emit_err: str | None = None
        emitted = 0
        for fn in post_emits:
            try:
                fn()
                emitted += 1
            except Exception as exc:  # noqa: BLE001
                emit_err = str(exc)
        return FoldResult(
            outcome="folded",
            field=field,
            provenance_id=prov_id,
            previous_value=prev_val,
            new_value=new_value,
            marker=marker,
            flag_detail=flag_detail,
            events_emitted=emitted,
            emit_error=emit_err,
        )
    except sqlite3.OperationalError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.isolation_level = saved_iso


def reverse_fold(
    conn: sqlite3.Connection,
    entity_id: str,
    field: str,
    provenance_id: str,
    *,
    expected_state_sha256: str | None = None,
) -> FoldResult:
    """Reverse a fold head record and restore the prior attribute value."""
    if field not in FOLD_FIELDS:
        return _refused("field_not_allowed", field)
    pre = _conn_precheck(conn)
    if pre:
        return _refused(pre, field)
    saved_iso = conn.isolation_level
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        entity = _read_entity(conn, entity_id)
        if entity is None:
            conn.rollback()
            return _refused("provenance_not_found", field)
        if expected_state_sha256 is not None:
            if state_sha256(conn, entity_id) != expected_state_sha256:
                conn.rollback()
                return _refused("stale_entity", field)
        attrs = _parse_attributes(entity.get("attributes"))
        sp_raw = attrs.get("state_provenance")
        state_provenance: dict[str, Any] = dict(sp_raw) if isinstance(sp_raw, dict) else {}
        records = list(state_provenance.get(field) or [])
        target = next((r for r in records if r.get("provenance_id") == provenance_id), None)
        if target is None:
            conn.rollback()
            return _refused("provenance_not_found", field)
        revs = {r.get("reverses") for r in records if r.get("kind") == "reversal"}
        if provenance_id in revs:
            conn.rollback()
            return _refused("already_reversed", field)
        head = provenance_head(records)
        if target.get("kind") != "fold" or head is None or head.get("provenance_id") != provenance_id:
            conn.rollback()
            return _refused("reverse_conflict", field)
        if field == "workflow_state":
            current = entity.get("workflow_state")
        else:
            current = attrs.get(field)
        if current != target.get("value"):
            conn.rollback()
            return _refused("reverse_conflict", field)
        restored = target.get("previous_value")
        if not target.get("previous_present"):
            restored = None
        rev_id = f"{field}:{len(records) + 1}"
        rev_record = {
            "provenance_id": rev_id,
            "kind": "reversal",
            "ref": f"provenance:{provenance_id}",
            "ref_kind": "provenance",
            "value": restored,
            "set_by": "reverse_fold",
            "set_at": _now_iso(),
            "reverses": provenance_id,
        }
        new_records = [*records, rev_record]
        new_sp = {**state_provenance, field: new_records}
        new_attrs = {**attrs, "state_provenance": new_sp}
        if field == "workflow_state":
            updates = {"workflow_state": restored, "attributes": new_attrs}
        else:
            new_attrs[field] = restored
            updates = {"attributes": new_attrs}
        post_emits: list[Callable[[], None]] = []

        def _emit_rev() -> None:
            cortex_entity_state_folded(
                entity_id=entity_id,
                field=field,
                provenance_id=rev_id,
                assertion_id=None,
                value=json.dumps(restored) if restored is not None else "null",
                change="reversed",
            )

        post_emits.append(_emit_rev)
        try:
            update_entity_impl(
                conn,
                entity_id=entity_id,
                updates=updates,
                commit=False,
                post_commit_emits=post_emits,
            )
        except HTTPException:
            conn.rollback()
            return FoldResult(outcome="refused", reason="gate_refused", field=field)
        conn.commit()
        emit_err: str | None = None
        emitted = 0
        for fn in post_emits:
            try:
                fn()
                emitted += 1
            except Exception as exc:  # noqa: BLE001
                emit_err = str(exc)
        return FoldResult(
            outcome="reversed",
            field=field,
            provenance_id=rev_id,
            previous_value=target.get("value"),
            new_value=restored,
            events_emitted=emitted,
            emit_error=emit_err,
        )
    except sqlite3.OperationalError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.isolation_level = saved_iso
