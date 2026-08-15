"""Correlation-only delivery-audit parents for nested cursor-sdk dispatches.

The B3 registry owns artifact audits, while this producer owns the parent
correlation row for a nested SDK execution.  A parent with no artifact children
is deliberately ``not-applicable``; it makes the request chain queryable
without claiming that the closeout body received an artifact-level audit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from event_store.delivery_audit_registry import (
    REGISTRY_SCHEMA_VERSION,
    connect,
    new_audit_id,
)
from universal_logging import get_logger

logger = get_logger(__name__)

_AUDIT_POLICY_VERSION = "dispatch-correlation-v1"
_PRODUCER_VERSION = "git_integration_worker"
_DISPATCH_SURFACE = "cursor-auto/nested"
_DISPATCH_ROLE = "cursor-sdk"
_DISPATCH_SEAT = "cursor-sdk"
_AGGREGATE_STATUS = "not-applicable"
_AGGREGATE_REASON_CODE = "correlation_only"
_AGGREGATE_REASON = (
    "Parent retained for request correlation; no artifact-level delivery audit "
    "was requested for this SDK dispatch."
)


@dataclass(frozen=True, slots=True)
class DispatchAuditContext:
    """Identity and dispatch metadata required by the parent registry row."""

    execution_id: str
    request_id: str | None
    dispatch_id: str
    thread_id: str
    model: str | None = None
    contract: str | None = None
    caller_agent: str | None = None


def context_from_ledger_row(row: Any) -> DispatchAuditContext | None:
    """Build nested-dispatch identity from a durable cursor-sdk ledger row."""
    if row is None:
        return None
    def value(key: str) -> Any:
        if hasattr(row, "get"):
            return row.get(key)
        if hasattr(row, "keys"):
            return row[key] if key in row.keys() else None
        return getattr(row, key, None)
    execution_id = value("execution_id")
    if not execution_id:
        return None
    try:
        record = json.loads(value("record_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        record = {}
    if not isinstance(record, dict) or record.get("admitted_via") != "cursor-auto":
        return None
    return DispatchAuditContext(
        execution_id=execution_id,
        request_id=record.get("request_id"),
        dispatch_id=value("dispatch_id"),
        thread_id=value("thread_id"),
        model=value("resolved_model"),
        contract=value("contract") or record.get("handoff_contract"),
        caller_agent=value("caller_agent") or record.get("caller_agent"),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _emit_opened(audit_id: str, context: DispatchAuditContext) -> None:
    try:
        from systems.pipeline.core.events.delivery_audit import (
            DeliveryAuditParentOpened,
        )

        from services.git_integration_worker.cursor_sdk_events import (
            emit_frontier_event,
        )

        emit_frontier_event(
            DeliveryAuditParentOpened(
                audit_id,
                execution_id=context.execution_id,
                request_id=context.request_id,
                dispatch_id=context.dispatch_id,
                registry_schema_version=REGISTRY_SCHEMA_VERSION,
                producer_version=_PRODUCER_VERSION,
            )
        )
    except Exception as exc:  # event visibility must not kill the dispatch
        logger.warning("delivery-audit parent opened event failed: %s", exc)


def _emit_finalized(audit_id: str, context: DispatchAuditContext) -> None:
    try:
        from systems.pipeline.core.events.delivery_audit import (
            DeliveryAuditParentFinalized,
        )

        from services.git_integration_worker.cursor_sdk_events import (
            emit_frontier_event,
        )

        emit_frontier_event(
            DeliveryAuditParentFinalized(
                audit_id,
                _AGGREGATE_STATUS,
                execution_id=context.execution_id,
                request_id=context.request_id,
                dispatch_id=context.dispatch_id,
                registry_schema_version=REGISTRY_SCHEMA_VERSION,
                producer_version=_PRODUCER_VERSION,
            )
        )
    except Exception as exc:  # event visibility must not kill terminalization
        logger.warning("delivery-audit parent finalized event failed: %s", exc)


def _emit_write_failed(
    context: DispatchAuditContext,
    *,
    audit_id: str | None,
    error: Exception,
) -> None:
    try:
        from systems.pipeline.core.events.delivery_audit import (
            DeliveryAuditRegistryWriteFailed,
        )

        from services.git_integration_worker.cursor_sdk_events import (
            emit_frontier_event,
        )

        emit_frontier_event(
            DeliveryAuditRegistryWriteFailed(
                audit_id=audit_id,
                execution_id=context.execution_id,
                request_id=context.request_id,
                dispatch_id=context.dispatch_id,
                error_code="registry_write_failed",
                error=f"{type(error).__name__}: {error}",
                registry_schema_version=REGISTRY_SCHEMA_VERSION,
                producer_version=_PRODUCER_VERSION,
            )
        )
    except Exception as exc:  # failure telemetry must not mask registry errors
        logger.warning("delivery-audit registry failure event failed: %s", exc)


def _assert_identity(row: Any, context: DispatchAuditContext) -> None:
    for field, expected in (
        ("execution_id", context.execution_id),
        ("request_id", context.request_id),
        ("dispatch_id", context.dispatch_id),
    ):
        observed = row[field]
        if observed and expected and observed != expected:
            raise ValueError(
                f"delivery-audit correlation conflict for {field}: "
                f"stored={observed!r} incoming={expected!r}"
            )


def _insert_parent(
    conn: Any,
    *,
    audit_id: str,
    context: DispatchAuditContext,
    timestamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO delivery_audits (
            audit_id, execution_id, request_id, dispatch_id,
            dispatch_surface, dispatch_contract, dispatch_role, dispatch_seat,
            model, dispatch_thread_id, agent_bus_thread_id, caller_agent,
            execution_started_at, audit_opened_at,
            aggregate_audit_status, aggregate_reason_code, aggregate_reason,
            artifact_count, auditable_artifact_count,
            registry_schema_version, audit_policy_version, producer_version,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_id,
            context.execution_id,
            context.request_id,
            context.dispatch_id,
            _DISPATCH_SURFACE,
            context.contract,
            _DISPATCH_ROLE,
            _DISPATCH_SEAT,
            context.model,
            context.thread_id,
            context.thread_id,
            context.caller_agent,
            timestamp,
            timestamp,
            _AGGREGATE_STATUS,
            _AGGREGATE_REASON_CODE,
            _AGGREGATE_REASON,
            0,
            0,
            REGISTRY_SCHEMA_VERSION,
            _AUDIT_POLICY_VERSION,
            _PRODUCER_VERSION,
            timestamp,
            timestamp,
        ),
    )


def open_dispatch_audit(context: DispatchAuditContext) -> str | None:
    """Open or reuse the parent row for one nested SDK execution."""
    audit_id: str | None = None
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM delivery_audits WHERE execution_id = ?",
                (context.execution_id,),
            ).fetchone()
            if row is not None:
                _assert_identity(row, context)
                return str(row["audit_id"])
            audit_id = new_audit_id()
            _insert_parent(
                conn,
                audit_id=audit_id,
                context=context,
                timestamp=_now(),
            )
            conn.commit()
        _emit_opened(audit_id, context)
        return audit_id
    except Exception as exc:  # audit visibility must not kill the dispatch
        logger.exception(
            "delivery-audit parent open failed: execution_id=%s dispatch_id=%s",
            context.execution_id,
            context.dispatch_id,
        )
        _emit_write_failed(context, audit_id=audit_id, error=exc)
        return None


def finalize_dispatch_audit(
    context: DispatchAuditContext,
    *,
    terminal_status: str,
) -> bool:
    """Stamp terminal time on the parent, creating it if start was missed."""
    audit_id: str | None = None
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM delivery_audits WHERE execution_id = ?",
                (context.execution_id,),
            ).fetchone()
            if row is None:
                audit_id = new_audit_id()
                timestamp = _now()
                _insert_parent(
                    conn,
                    audit_id=audit_id,
                    context=context,
                    timestamp=timestamp,
                )
            else:
                audit_id = str(row["audit_id"])
                _assert_identity(row, context)
                timestamp = _now()
            conn.execute(
                """
                UPDATE delivery_audits
                SET execution_completed_at = COALESCE(execution_completed_at, ?),
                    audit_finalized_at = COALESCE(audit_finalized_at, ?),
                    aggregate_reason_code = CASE
                        WHEN aggregate_reason_code = ? THEN ?
                        ELSE aggregate_reason_code
                    END,
                    aggregate_reason = COALESCE(aggregate_reason, ?),
                    updated_at = ?
                WHERE audit_id = ?
                """,
                (
                    timestamp,
                    timestamp,
                    _AGGREGATE_REASON_CODE,
                    f"{_AGGREGATE_REASON_CODE}:{terminal_status}",
                    _AGGREGATE_REASON,
                    timestamp,
                    audit_id,
                ),
            )
            conn.commit()
        if row is None:
            _emit_opened(audit_id, context)
        _emit_finalized(audit_id, context)
        return True
    except Exception as exc:  # audit visibility must not kill terminalization
        logger.exception(
            "delivery-audit parent finalize failed: execution_id=%s dispatch_id=%s",
            context.execution_id,
            context.dispatch_id,
        )
        _emit_write_failed(context, audit_id=audit_id, error=exc)
        return False
