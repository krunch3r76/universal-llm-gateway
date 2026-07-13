"""Apply frozen imprint op plans — atomic claim + execute_op semantics on one conn."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException

from ..db import cortex_conn
from ..dispatch_ops import execute_op
from ..models import RelationshipCreate
from ..routes.relationships import create_relationship_on_conn
from .proposal_store import commit_reject_code, get_proposal, mark_committed


class ImprintCommitError(Exception):
    """Typed commit reject carrying ProtocolError envelope fields."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 422,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.data = data or {}
        self.source = "cortex-api"
        self.retryable = False
        super().__init__(message)


class _DeferredCommitConn:
    """Proxy conn: writes on shared txn, suppresses inner commit/close."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None


def _entity_crud():
    from ..entity_crud import create_entity_impl, update_entity_impl

    return create_entity_impl, update_entity_impl


def _execute_plan_entry(
    conn: sqlite3.Connection,
    entry: dict[str, Any],
    *,
    deferred_emits: list[Callable[[], None]] | None = None,
) -> dict[str, Any]:
    """Mirror ``execute_op`` for life-imprint plan entries on ``conn``."""
    op = str(entry.get("op") or "")
    args = dict(entry.get("args") or {})
    deferred = deferred_emits if deferred_emits is not None else []

    if op == "entity_create":
        create_entity_impl, _ = _entity_crud()
        return create_entity_impl(conn, args, commit=False)

    if op == "entity_update":
        _, update_entity_impl = _entity_crud()
        entity_id = args.pop("entity_id", None)
        if not entity_id:
            raise ImprintCommitError(
                "apply_failed",
                "entity_id is required for entity_update",
                data={"op_index": None, "op": op, "detail": "missing entity_id"},
            )
        try:
            return update_entity_impl(
                conn,
                entity_id=str(entity_id),
                updates=args,
                commit=False,
                post_commit_emits=deferred,
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            raise ImprintCommitError(
                "apply_failed",
                detail,
                data={"op": op, "detail": detail},
            ) from exc

    if op == "relationship_create":
        try:
            body = RelationshipCreate.model_validate(args)
            result = create_relationship_on_conn(
                conn, body, commit=False, post_commit_emits=deferred
            )
            return result.model_dump(mode="json")
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            raise ImprintCommitError(
                "apply_failed",
                detail,
                data={"op": op, "detail": detail},
            ) from exc

    if op == "assert":
        return _execute_assert_on_conn(conn, args)

    raise ImprintCommitError(
        "apply_failed",
        f"Unknown imprint op {op!r}",
        data={"op": op, "detail": "unknown op"},
    )


@contextmanager
def _patched_cortex_conn(conn: sqlite3.Connection) -> Iterator[None]:
    proxy = _DeferredCommitConn(conn)
    import cortex_store.db as db_mod
    import cortex_store.routes.assertions._create as assert_create

    originals = (
        db_mod.cortex_conn,
        assert_create.cortex_conn,
    )
    db_mod.cortex_conn = lambda: proxy  # type: ignore[assignment]
    assert_create.cortex_conn = lambda: proxy  # type: ignore[assignment]
    try:
        yield
    finally:
        db_mod.cortex_conn = originals[0]
        assert_create.cortex_conn = originals[1]


def _execute_assert_on_conn(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    from ..routes.assertions._create import _create_assertion_impl

    with _patched_cortex_conn(conn):
        result = _create_assertion_impl(args)
    if isinstance(result, dict) and result.get("error"):
        raise ImprintCommitError(
            "apply_failed",
            str(result["error"]),
            data={"op": "assert", "detail": result["error"]},
        )
    return result


def commit_imprint_proposal(proposal_id: str) -> dict[str, Any]:
    """Atomically claim, apply frozen op_plan, and mark proposal committed."""
    deferred_emits: list[Callable[[], None]] = []
    with cortex_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = get_proposal(conn, proposal_id)
            reject = commit_reject_code(row)
            if reject:
                conn.rollback()
                raise ImprintCommitError(
                    reject,
                    reject.replace("_", " "),
                )

            assert row is not None
            op_plan = row.get("op_plan") or []
            applied: list[dict[str, Any]] = []
            for index, entry in enumerate(op_plan):
                try:
                    result = _execute_plan_entry(
                        conn, entry, deferred_emits=deferred_emits
                    )
                except ImprintCommitError as exc:
                    conn.rollback()
                    exc.data = {**exc.data, "op_index": index, "op": entry.get("op")}
                    raise
                except HTTPException as exc:
                    conn.rollback()
                    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                    raise ImprintCommitError(
                        "apply_failed",
                        detail,
                        data={
                            "op_index": index,
                            "op": entry.get("op"),
                            "detail": detail,
                        },
                    ) from exc
                except sqlite3.Error as exc:
                    conn.rollback()
                    raise ImprintCommitError(
                        "apply_failed",
                        str(exc),
                        data={
                            "op_index": index,
                            "op": entry.get("op"),
                            "detail": str(exc),
                        },
                    ) from exc
                applied.append({"op": entry.get("op"), "result": result})

            if not mark_committed(conn, proposal_id, applied=applied):
                conn.rollback()
                raise ImprintCommitError(
                    "proposal_already_committed",
                    "proposal already committed",
                )

            conn.commit()
        except ImprintCommitError:
            raise
        except Exception:
            conn.rollback()
            raise

    for emit in deferred_emits:
        emit()

    return {
        "proposal_id": proposal_id,
        "applied": applied,
        "context": "cortex.life/v1",
    }


def dry_run_execute_op(tool: str, arguments: object) -> Any:
    """Expose execute_op for tests that verify dispatch parity."""
    return execute_op(tool, arguments)


__all__ = [
    "ImprintCommitError",
    "commit_imprint_proposal",
    "dry_run_execute_op",
]
