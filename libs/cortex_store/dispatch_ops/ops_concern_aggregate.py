"""Dispatch ops: concern_transition (write) and concern_rebuild (admin/test)."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from ..concern_aggregate.cas import ConcernCasError
from ..concern_aggregate.checkpoint import write_checkpoint
from ..concern_aggregate.journal import TransitionRejectedError, append_transition
from ..concern_aggregate.recovery import rebuild_concern
from ..db import cortex_conn
from ._shared import record

logger = get_logger("cortex-api.dispatch_ops.concern_aggregate")


def _op_concern_transition(
    concern_id: str | None = None,
    kind: str | None = None,
    record_ref: str | None = None,
    authorized_by: str | None = None,
    payload: dict[str, Any] | None = None,
    intent_id: str | None = None,
    request_id: str | None = None,
    delivery_id: str | None = None,
    emit_advisory: bool = True,
    expected_revision: int | None = None,
    write_intent_id: str | None = None,
    material: dict[str, Any] | None = None,
    **_: object,
) -> dict[str, Any]:
    if not concern_id or not kind or not record_ref or not authorized_by:
        return {
            "error": "concern_id, kind, record_ref, and authorized_by are required",
        }
    conn = cortex_conn()
    try:
        result = append_transition(
            conn,
            concern_id=concern_id,
            kind=kind,
            payload=payload,
            record_ref=record_ref,
            authorized_by=authorized_by,
            intent_id=intent_id,
            request_id=request_id,
            delivery_id=delivery_id,
            emit_advisory=bool(emit_advisory),
            expected_revision=expected_revision,
            write_intent_id=write_intent_id,
            material=material,
        )
        record(
            "mcp.cortex.concern_transition",
            concern_id=concern_id,
            seq=result.get("seq"),
            kind=kind,
        )
        return result
    except ConcernCasError as exc:
        return exc.payload
    except TransitionRejectedError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def _op_concern_rebuild(
    concern_id: str | None = None,
    checkpoint: bool = False,
    **_: object,
) -> dict[str, Any]:
    """Admin/test rebuild from checkpoint + committed suffix."""
    if not concern_id:
        return {"error": "concern_id is required"}
    conn = cortex_conn()
    try:
        if checkpoint:
            write_checkpoint(conn, concern_id=concern_id)
        state = rebuild_concern(conn, concern_id)
        return {"concern_id": concern_id, "state": state}
    finally:
        conn.close()

