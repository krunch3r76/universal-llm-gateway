"""Baseline collection harness for delivery-audit token-locality campaigns."""

from __future__ import annotations

import math
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .delivery_audit_baseline_reports import (
    fetch_workflow_summaries,
    summarize_baseline_campaign,
)
from .delivery_audit_baseline_types import (
    INPUT_TOKEN_FIELDS,
    VALID_PHASES,
    VALID_SEAT_SUBSTRATES,
    VALID_WORKFLOW_CLASSES,
    BaselineArtifact,
    BaselineTrace,
    require_known,
)
from .delivery_audit_guidance_keys import (
    derive_guidance_resource_key,
    guidance_projection_surface,
)
from .delivery_audit_registry import (
    REGISTRY_SCHEMA_VERSION,
    VALID_ARTIFACT_CLASSES,
    artifact_identity_key,
    connect,
    content_digest,
    derive_aggregate_audit_status,
    derive_token_rollups,
    new_artifact_record_id,
    new_audit_id,
)

__all__ = [
    "BaselineArtifact",
    "BaselineTrace",
    "fetch_workflow_summaries",
    "record_baseline_trace",
    "summarize_baseline_campaign",
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_workflow_summary_id() -> str:
    return str(uuid.uuid4())


def _body_bytes(body: str | bytes) -> bytes:
    return body.encode("utf-8") if isinstance(body, str) else body


def _estimate_tokens(body: str | bytes) -> int:
    size = len(_body_bytes(body))
    return 0 if size == 0 else max(1, math.ceil(size / 4))


def _input_tokens(rollups: dict[str, int]) -> int:
    return sum(int(rollups[field]) for field in INPUT_TOKEN_FIELDS)


def _derive_artifact_row(
    *,
    audit_id: str,
    artifact: BaselineArtifact,
    sequence: int,
    seen_keys: set[str],
    timestamp: str,
) -> dict[str, Any]:
    require_known(artifact.artifact_class, VALID_ARTIFACT_CLASSES, "artifact_class")
    body = _body_bytes(artifact.body)
    projection_surface = artifact.projection_surface or guidance_projection_surface(
        artifact_class=artifact.artifact_class,
        source_uri=artifact.source_uri,
        tool_surface=artifact.tool_surface,
    )
    resource_key = artifact.guidance_resource_key or derive_guidance_resource_key(
        artifact_class=artifact.artifact_class,
        artifact_id=artifact.artifact_id,
        projection_surface=projection_surface,
        affordance_kind=artifact.affordance_kind,
    )
    is_duplicate = 1 if resource_key in seen_keys else 0
    seen_keys.add(resource_key)
    rendered_hash = artifact.rendered_content_hash or (
        content_digest(body) if body else None
    )
    delivered_tokens = (
        artifact.delivered_tokens
        if artifact.delivered_tokens is not None
        else _estimate_tokens(body)
    )
    identity = artifact_identity_key(
        audit_id=audit_id,
        artifact_class=artifact.artifact_class,
        artifact_id=artifact.artifact_id,
        artifact_version=artifact.artifact_version,
        delivery_step=artifact.delivery_step,
        recipient_scope=artifact.recipient_scope,
    )
    return {
        "artifact_record_id": new_artifact_record_id(),
        "audit_id": audit_id,
        "artifact_sequence": sequence,
        "artifact_identity_key": identity,
        "artifact_class": artifact.artifact_class,
        "artifact_id": artifact.artifact_id,
        "artifact_version": artifact.artifact_version,
        "artifact_revision": artifact.artifact_revision,
        "artifact_title": artifact.artifact_title,
        "delivery_step": artifact.delivery_step,
        "recipient_scope": artifact.recipient_scope,
        "recipient_agent": artifact.recipient_agent,
        "recipient_surface": artifact.recipient_surface,
        "source_uri": artifact.source_uri,
        "source_content_hash": artifact.source_content_hash,
        "rendered_uri": artifact.rendered_uri,
        "rendered_content_hash": rendered_hash,
        "rendered_content_length": len(body),
        "rendered_mime_type": artifact.rendered_mime_type,
        "audit_status": artifact.audit_status,
        "audit_reason_code": artifact.audit_reason_code,
        "audit_reason": artifact.audit_reason,
        "provider": artifact.provider,
        "model": artifact.model,
        "dispatch_contract": artifact.dispatch_contract,
        "dispatch_role": artifact.dispatch_role,
        "affordance_kind": artifact.affordance_kind,
        "tool_surface": artifact.tool_surface,
        "guidance_resource_key": resource_key,
        "projection_surface": projection_surface,
        "content_digest": content_digest(body) if body else None,
        "delivered_bytes": len(body),
        "delivered_tokens": delivered_tokens,
        "fetch_scope": artifact.fetch_scope,
        "token_category": artifact.token_category,
        "is_duplicate": is_duplicate,
        "dedup_scope": artifact.dedup_scope,
        "whole_doc_reason": artifact.whole_doc_reason,
        "restated_overlap_tokens": artifact.restated_overlap_tokens,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _insert_artifact(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    columns = tuple(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"""
        INSERT INTO delivered_artifacts ({", ".join(columns)})
        VALUES ({placeholders})
        """,
        tuple(row[column] for column in columns),
    )


def _summary_from_rows(
    *,
    workflow_summary_id: str,
    audit_id: str,
    execution_id: str,
    trace: BaselineTrace,
    rows: list[dict[str, Any]],
    timestamp: str,
) -> dict[str, Any]:
    rollups = derive_token_rollups(rows)
    return {
        "workflow_summary_id": workflow_summary_id,
        "audit_id": audit_id,
        "execution_id": execution_id,
        "workflow_class": trace.workflow_class,
        "phase": trace.phase,
        "campaign_id": trace.campaign_id,
        "seat_substrate": trace.seat_substrate,
        "input_tokens": _input_tokens(rollups),
        **rollups,
        "artifact_count": len(rows),
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _insert_summary(conn: sqlite3.Connection, summary: dict[str, Any]) -> None:
    columns = tuple(summary.keys())
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"""
        INSERT INTO guidance_workflow_summaries ({", ".join(columns)})
        VALUES ({placeholders})
        """,
        tuple(summary[column] for column in columns),
    )


def record_baseline_trace(
    trace: BaselineTrace,
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Record one tagged workflow trace and its derived workflow summary."""
    require_known(trace.workflow_class, VALID_WORKFLOW_CLASSES, "workflow_class")
    require_known(trace.phase, VALID_PHASES, "phase")
    require_known(trace.seat_substrate, VALID_SEAT_SUBSTRATES, "seat_substrate")
    if not trace.campaign_id:
        raise ValueError("campaign_id is required")
    if not trace.artifacts:
        raise ValueError("at least one artifact is required")

    timestamp = _now()
    audit_id = new_audit_id()
    execution_id = trace.execution_id or f"{trace.campaign_id}:{audit_id}"
    seen_keys: set[str] = set()
    rows = [
        _derive_artifact_row(
            audit_id=audit_id,
            artifact=artifact,
            sequence=sequence,
            seen_keys=seen_keys,
            timestamp=timestamp,
        )
        for sequence, artifact in enumerate(trace.artifacts, start=1)
    ]
    aggregate_status = derive_aggregate_audit_status(
        [row["audit_status"] for row in rows],
        covered=True,
    )
    summary = _summary_from_rows(
        workflow_summary_id=_new_workflow_summary_id(),
        audit_id=audit_id,
        execution_id=execution_id,
        trace=trace,
        rows=rows,
        timestamp=timestamp,
    )

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO delivery_audits (
                audit_id, execution_id, request_id, dispatch_id, pipeline_id,
                dispatch_surface, dispatch_contract, dispatch_role, dispatch_seat,
                provider, model, dispatch_thread_id, agent_bus_thread_id,
                caller_agent, transcript_id, audit_opened_at,
                aggregate_audit_status, artifact_count, auditable_artifact_count,
                registry_schema_version, audit_policy_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                execution_id,
                trace.request_id,
                trace.dispatch_id,
                trace.pipeline_id,
                trace.dispatch_surface,
                trace.dispatch_contract,
                trace.dispatch_role,
                trace.dispatch_seat,
                trace.provider,
                trace.model,
                trace.dispatch_thread_id,
                trace.agent_bus_thread_id,
                trace.caller_agent,
                trace.transcript_id,
                timestamp,
                aggregate_status,
                len(rows),
                len(rows),
                REGISTRY_SCHEMA_VERSION,
                "token-locality-baseline-v1",
                timestamp,
                timestamp,
            ),
        )
        for row in rows:
            _insert_artifact(conn, row)
        _insert_summary(conn, summary)
        conn.commit()

    return {
        "audit_id": audit_id,
        "execution_id": execution_id,
        "workflow_summary": summary,
        "artifact_count": len(rows),
    }
