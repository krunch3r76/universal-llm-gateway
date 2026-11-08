"""Transaction helpers and W4 fold/reverse write path."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException
from universal_logging import get_logger

from cortex_store.card_temporal import card_as_of_utc, install_card_temporal_function
from cortex_store.entity_crud import update_entity_impl
from cortex_store.entity_state_flag_rule import flag_fold_marker, flag_withholds
from cortex_store.entity_state_fold_gate import ResolveUtterance, gate_refusal
from cortex_store.entity_state_provenance import (
    DENYLIST_FIELDS,
    FOLD_FIELDS,
    FoldResult,
    attr_value_present,
    claim_binds_field,
    derive_fold_value,
    encode_fold_event_value,
    fold_field_updates,
    parse_attributes,
    parse_status_current,
    provenance_head,
    state_sha256,
    utc_now_iso,
    values_match_strict,
)
from cortex_store.entity_state_read import read_assertion, read_entity
from cortex_store.event_publisher import cortex_entity_state_folded

logger = get_logger("cortex_store.entity_state_fold")

@contextmanager
def fold_write_lock(conn: sqlite3.Connection) -> Generator[None, None, None]:
    saved_iso = conn.isolation_level
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        yield
    finally:
        conn.isolation_level = saved_iso


def commit_and_emit(
    conn: sqlite3.Connection, post_emits: list[Callable[[], None]]
) -> tuple[int, str | None]:
    conn.commit()
    errors: list[str] = []
    emitted = 0
    for fn in post_emits:
        try:
            fn()
            emitted += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("entity_state fold emit failed: %s", exc)
            errors.append(str(exc))
    return emitted, "; ".join(errors) if errors else None


def gate_refused_reason(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, str):
        return f"gate_refused: {detail}"
    return f"gate_refused: {json.dumps(detail)}"


def conn_precheck(conn: sqlite3.Connection) -> str | None:
    if getattr(conn, "in_transaction", False):
        return "conn_in_transaction"
    if getattr(conn, "autocommit", None) is True:
        return "conn_isolation_unsupported"
    return None


def _refused(reason: str, field: str | None = None) -> FoldResult:
    return FoldResult(outcome="refused", reason=reason, field=field)


def _commit_writes(
    conn: sqlite3.Connection,
    entity_id: str,
    field: str,
    updates: dict[str, object],
    post_emits: list[Callable[[], None]],
) -> FoldResult | tuple[int, str | None]:
    try:
        update_entity_impl(
            conn,
            entity_id=entity_id,
            updates=updates,
            commit=False,
            post_commit_emits=post_emits,
        )
    except HTTPException as exc:
        conn.rollback()
        return _refused(gate_refused_reason(exc), field)
    return commit_and_emit(conn, post_emits)


def fold_claim(
    conn: sqlite3.Connection,
    entity_id: str,
    assertion_id: int | None,
    field: str,
    *,
    expected_state_sha256: str | None = None,
    resolver: ResolveUtterance | None = None,
) -> FoldResult:
    if assertion_id is None:
        return _refused("assertion_id_required", field)
    if field in DENYLIST_FIELDS or field not in FOLD_FIELDS:
        return _refused("field_not_allowed", field)
    pre = conn_precheck(conn)
    if pre:
        return _refused(pre, field)
    install_card_temporal_function(conn)
    try:
        with fold_write_lock(conn):
            entity = read_entity(conn, entity_id)
            if entity is None:
                conn.rollback()
                return _refused("stale_entity", field)
            claim = read_assertion(conn, assertion_id)
            if claim is None:
                conn.rollback()
                return _refused("superseded", field)
            if expected_state_sha256 is not None and state_sha256(conn, entity_id) != expected_state_sha256:
                conn.rollback()
                return _refused("stale_entity", field)
            attrs = parse_attributes(entity.get("attributes"))
            sp_raw = attrs.get("state_provenance")
            state_provenance: dict[str, Any] = dict(sp_raw) if isinstance(sp_raw, dict) else {}
            if flag_withholds(claim.get("review_status"), claim.get("review_notes"), claim):
                conn.rollback()
                return FoldResult(outcome="withheld", reason=str(claim.get("review_notes") or ""), field=field)
            parsed = parse_status_current(str(claim.get("predicate_form") or ""))
            if parsed is None:
                conn.rollback()
                return _refused("predicate_mismatch", field)
            bound_entity, token = parsed
            if bound_entity != entity_id or str(claim.get("entity_id")) != entity_id:
                conn.rollback()
                return _refused("claim_entity_mismatch", field)
            if not claim_binds_field(field, entity_id, str(claim.get("predicate_form") or ""), token):
                conn.rollback()
                return _refused("predicate_mismatch", field)
            new_value = derive_fold_value(field, token)
            if new_value is None:
                conn.rollback()
                return _refused("predicate_mismatch", field)
            refusal = gate_refusal(
                conn,
                entity_id=entity_id,
                entity_type=str(entity.get("type") or ""),
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
            marker, flag_detail = flag_fold_marker(claim.get("review_status"), claim.get("review_notes"))
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
                prev_val, prev_present = entity.get("workflow_state"), entity.get("workflow_state") is not None
            else:
                prev_val, prev_present = attr_value_present(attrs, field)
            prov_id = f"{field}:{len(records) + 1}"
            fold_record: dict[str, Any] = {
                "provenance_id": prov_id,
                "kind": "fold",
                "ref": f"assertion:{assertion_id}",
                "ref_kind": "assertion",
                "value": new_value,
                "set_by": "fold",
                "set_at": utc_now_iso(),
                "assertion_id": assertion_id,
                "previous_value": prev_val,
                "previous_present": prev_present,
                "marker": marker,
                "flag_detail": flag_detail,
            }
            updates = fold_field_updates(field, attrs, state_provenance, [*records, fold_record], new_value)
            post_emits: list[Callable[[], None]] = [
                lambda: cortex_entity_state_folded(
                    entity_id=entity_id,
                    field=field,
                    provenance_id=prov_id,
                    assertion_id=assertion_id,
                    value=encode_fold_event_value(new_value),
                )
            ]
            committed = _commit_writes(conn, entity_id, field, updates, post_emits)
            if isinstance(committed, FoldResult):
                return committed
            emitted, emit_err = committed
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


def reverse_fold(
    conn: sqlite3.Connection,
    entity_id: str,
    field: str,
    provenance_id: str,
    *,
    expected_state_sha256: str | None = None,
) -> FoldResult:
    if field not in FOLD_FIELDS:
        return _refused("field_not_allowed", field)
    pre = conn_precheck(conn)
    if pre:
        return _refused(pre, field)
    try:
        with fold_write_lock(conn):
            entity = read_entity(conn, entity_id)
            if entity is None:
                conn.rollback()
                return _refused("provenance_not_found", field)
            if expected_state_sha256 is not None and state_sha256(conn, entity_id) != expected_state_sha256:
                conn.rollback()
                return _refused("stale_entity", field)
            attrs = parse_attributes(entity.get("attributes"))
            sp_raw = attrs.get("state_provenance")
            state_provenance: dict[str, Any] = dict(sp_raw) if isinstance(sp_raw, dict) else {}
            records = list(state_provenance.get(field) or [])
            if sum(1 for r in records if r.get("provenance_id") == provenance_id) > 1:
                conn.rollback()
                return _refused("reverse_conflict", field)
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
            current = entity.get("workflow_state") if field == "workflow_state" else attrs.get(field)
            if not values_match_strict(current, target.get("value")):
                conn.rollback()
                return _refused("reverse_conflict", field)
            restored = target.get("previous_value") if target.get("previous_present") else None
            rev_id = f"{field}:{len(records) + 1}"
            rev_record = {
                "provenance_id": rev_id,
                "kind": "reversal",
                "ref": f"provenance:{provenance_id}",
                "ref_kind": "provenance",
                "value": restored,
                "set_by": "reverse_fold",
                "set_at": utc_now_iso(),
                "reverses": provenance_id,
            }
            updates = fold_field_updates(field, attrs, state_provenance, [*records, rev_record], restored)
            post_emits: list[Callable[[], None]] = [
                lambda: cortex_entity_state_folded(
                    entity_id=entity_id,
                    field=field,
                    provenance_id=rev_id,
                    assertion_id=None,
                    value=encode_fold_event_value(restored),
                    change="reversed",
                )
            ]
            committed = _commit_writes(conn, entity_id, field, updates, post_emits)
            if isinstance(committed, FoldResult):
                return committed
            emitted, emit_err = committed
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
