"""Sibling SQLite registry for B3 delivered-artifact delivery audits.

Storage lives at ``DATA_DIR/delivery-audit.db``, separate from the Event Service
event-store database. Event Service named operations read through this module;
registry writes remain here for future producer wiring.

Schema version 3 adds workflow-summary rows for baseline campaign measurement.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .delivery_audit_guidance_keys import (
    VALID_PROJECTION_SURFACES,
    class_qualified_guidance_resource_key,
    derive_guidance_resource_key,
    guidance_projection_surface,
    guidance_resource_key,
)
from .delivery_audit_schema import connect, delivery_audit_db_path, ensure_schema

REGISTRY_SCHEMA_VERSION = "3"

VALID_AUDIT_STATUSES = frozenset(
    {
        "audited-clean",
        "audit-divergent",
        "unaudited",
        "not-applicable",
        "write-failed",
    }
)

VALID_ARTIFACT_CLASSES = frozenset(
    {
        "http_rule_body",
        "http_skill_body",
        "boot_card_block",
        "tool_fol_descriptor",
        "provider_affordance_surface",
    }
)

VALID_CAPABILITY_CLASSES = frozenset(
    {
        "provider_native",
        "provider_declared",
        "adapter_emulated",
        "ulg_workflow_wrapper",
        "unknown",
    }
)

VALID_SUPPORT_MODES = frozenset(
    {
        "native",
        "emulated",
        "unsupported",
        "withheld",
        "unknown",
    }
)

VALID_CAPABILITY_EVENT_TYPES = frozenset(
    {
        "declared_available",
        "requested",
        "delivered",
        "activated",
        "used",
        "withheld",
        "fallback_emulated",
    }
)

__all__ = [
    "REGISTRY_SCHEMA_VERSION",
    "VALID_ARTIFACT_CLASSES",
    "VALID_AUDIT_STATUSES",
    "VALID_CAPABILITY_CLASSES",
    "VALID_CAPABILITY_EVENT_TYPES",
    "VALID_PROJECTION_SURFACES",
    "VALID_SUPPORT_MODES",
    "artifact_identity_key",
    "class_qualified_guidance_resource_key",
    "connect",
    "content_digest",
    "delivery_audit_db_path",
    "derive_aggregate_audit_status",
    "derive_child_audit_status",
    "derive_guidance_resource_key",
    "derive_token_rollups",
    "ensure_schema",
    "fetch_parent_by_audit_id",
    "fetch_parent_by_correlation",
    "guidance_projection_surface",
    "guidance_resource_key",
    "list_artifacts_for_audit",
    "new_artifact_record_id",
    "new_audit_id",
    "validate_provider_affordance_surface_fields",
    "validate_whole_doc_reason",
]


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def artifact_identity_key(
    *,
    audit_id: str,
    artifact_class: str,
    artifact_id: str,
    artifact_version: str | None = None,
    delivery_step: str,
    recipient_scope: str | None = None,
) -> str:
    """Return the deterministic child idempotency key for one delivered artifact."""
    payload = [
        audit_id,
        artifact_class,
        artifact_id,
        artifact_version or "",
        delivery_step,
        recipient_scope or "",
    ]
    canonical = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _coalesce_int(value: Any) -> int:
    return 0 if value is None else int(value)


def content_digest(body: str | bytes) -> str:
    """Return sha256 hex of the delivered body; encodes ``str`` as UTF-8."""
    payload = body.encode("utf-8") if isinstance(body, str) else body
    return hashlib.sha256(payload).hexdigest()


def _is_present(value: Any) -> bool:
    return value is not None and value != ""


def validate_provider_affordance_surface_fields(row: dict[str, Any]) -> None:
    """Validate provider-affordance columns for ``provider_affordance_surface`` rows."""
    if row.get("artifact_class") != "provider_affordance_surface":
        return

    for field, valid in (
        ("capability_class", VALID_CAPABILITY_CLASSES),
        ("support_mode", VALID_SUPPORT_MODES),
        ("capability_event_type", VALID_CAPABILITY_EVENT_TYPES),
    ):
        value = row.get(field)
        if value is not None and value not in valid:
            raise ValueError(f"unknown {field}: {value!r}")

    authority_delta = row.get("authority_delta")
    if authority_delta is not None and authority_delta == "":
        raise ValueError("authority_delta must be non-empty when present")


def _provider_affordance_clean_evidence_complete(
    row: dict[str, Any],
    *,
    audit_policy_version: str | None,
) -> bool:
    recipient_ok = (
        _is_present(row.get("recipient_agent"))
        or row.get("recipient_scope") == "global"
    )
    identity_ok = _is_present(row.get("affordance_kind")) or _is_present(
        row.get("capability_key")
    )
    required = (
        row.get("provider"),
        row.get("model"),
        recipient_ok,
        row.get("artifact_id"),
        identity_ok,
        row.get("capability_class"),
        row.get("support_mode"),
        row.get("effect_axes"),
        row.get("rendered_content_hash"),
        row.get("audit_checker"),
        row.get("audit_checker_version"),
        audit_policy_version,
        row.get("audit_evidence_uris"),
    )
    return all(
        value if isinstance(value, bool) else _is_present(value) for value in required
    )


def derive_child_audit_status(
    row: dict[str, Any],
    *,
    audit_policy_version: str | None = None,
) -> str:
    """Derive per-row audit status for aggregate rollup."""
    stored = row.get("audit_status", "unaudited")
    if stored in ("write-failed", "audit-divergent"):
        return stored
    if row.get("artifact_class") != "provider_affordance_surface":
        return stored
    if _provider_affordance_clean_evidence_complete(
        row,
        audit_policy_version=audit_policy_version,
    ):
        return "audited-clean"
    return "unaudited"


def validate_whole_doc_reason(rows: list[dict]) -> list[str]:
    """Return row ids where ``fetch_scope`` is whole_doc but ``whole_doc_reason`` is absent."""
    flagged: list[str] = []
    for row in rows:
        if row.get("fetch_scope") != "whole_doc":
            continue
        reason = row.get("whole_doc_reason")
        if reason is None or reason == "":
            flagged.append(
                row.get("artifact_record_id") or row.get("artifact_identity_key", "")
            )
    return flagged


def derive_token_rollups(rows: list[dict]) -> dict[str, int]:
    """Derive grouping-agnostic token and byte rollups; NULL numerics coalesce to 0.

    ``section_bytes`` aggregates bounded fetch scopes:
    ``section``, ``checklist``, and ``synthesized_bundle``.
    ``trigger_fan_in_count`` is the maximum row count sharing one
    ``guidance_resource_key`` within ``rows``.
    """
    resident_guidance_tokens = 0
    fetched_guidance_tokens = 0
    duplicate_guidance_tokens = 0
    tool_schema_tokens = 0
    task_corpus_tokens = 0
    transcript_restated_tokens = 0
    restated_overlap_tokens_total = 0
    whole_doc_bytes = 0
    section_bytes = 0
    key_counts: dict[Any, int] = {}

    for row in rows:
        tokens = _coalesce_int(row.get("delivered_tokens"))
        token_category = row.get("token_category")
        fetch_scope = row.get("fetch_scope")
        delivered_bytes = _coalesce_int(row.get("delivered_bytes"))

        if token_category == "resident_guidance":
            resident_guidance_tokens += tokens
        elif token_category == "fetched_guidance":
            fetched_guidance_tokens += tokens
        elif token_category == "tool_schema_discovery":
            tool_schema_tokens += tokens
        elif token_category == "task_corpus":
            task_corpus_tokens += tokens
        elif token_category == "transcript_restated_guidance":
            transcript_restated_tokens += tokens

        if row.get("is_duplicate") == 1:
            duplicate_guidance_tokens += tokens

        restated_overlap_tokens_total += _coalesce_int(
            row.get("restated_overlap_tokens")
        )

        if fetch_scope == "whole_doc":
            whole_doc_bytes += delivered_bytes
        elif fetch_scope in ("section", "checklist", "synthesized_bundle"):
            section_bytes += delivered_bytes

        key = row.get("guidance_resource_key")
        if key:
            key_counts[key] = key_counts.get(key, 0) + 1

    trigger_fan_in_count = max(key_counts.values()) if key_counts else 0

    return {
        "resident_guidance_tokens": resident_guidance_tokens,
        "fetched_guidance_tokens": fetched_guidance_tokens,
        "duplicate_guidance_tokens": duplicate_guidance_tokens,
        "tool_schema_tokens": tool_schema_tokens,
        "task_corpus_tokens": task_corpus_tokens,
        "transcript_restated_tokens": transcript_restated_tokens,
        "restated_overlap_tokens_total": restated_overlap_tokens_total,
        "whole_doc_bytes": whole_doc_bytes,
        "section_bytes": section_bytes,
        "trigger_fan_in_count": trigger_fan_in_count,
    }


def derive_aggregate_audit_status(
    child_statuses: list[str],
    *,
    covered: bool,
    registry_write_failed: bool = False,
) -> str:
    """Derive parent aggregate status from child audit statuses."""
    if registry_write_failed:
        return "write-failed"

    for status in child_statuses:
        if status not in VALID_AUDIT_STATUSES:
            raise ValueError(f"unknown audit status: {status!r}")

    if not child_statuses:
        return "unaudited" if covered else "not-applicable"

    if any(status == "write-failed" for status in child_statuses):
        return "write-failed"
    if any(status == "audit-divergent" for status in child_statuses):
        return "audit-divergent"
    if any(status == "unaudited" for status in child_statuses):
        return "unaudited"
    if all(status == "not-applicable" for status in child_statuses):
        return "not-applicable"
    if any(status == "audited-clean" for status in child_statuses) and all(
        status in ("audited-clean", "not-applicable") for status in child_statuses
    ):
        return "audited-clean"

    raise ValueError(f"cannot derive aggregate from child statuses: {child_statuses!r}")


def fetch_parent_by_audit_id(
    audit_id: str,
    *,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Return one parent row by ``audit_id``, or ``None`` when absent."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM delivery_audits WHERE audit_id = ?",
            (audit_id,),
        ).fetchone()
    return _row_to_dict(row)


def fetch_parent_by_correlation(
    *,
    execution_id: str | None = None,
    request_id: str | None = None,
    dispatch_id: str | None = None,
    db_path: Path | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return one parent row looked up by a single correlation key."""
    provided = {
        key: value
        for key, value in (
            ("execution_id", execution_id),
            ("request_id", request_id),
            ("dispatch_id", dispatch_id),
        )
        if value
    }
    if len(provided) != 1:
        raise ValueError("exactly one correlation key is required")

    lookup_key, lookup_value = next(iter(provided.items()))
    with connect(db_path) as conn:
        row = conn.execute(
            f"SELECT * FROM delivery_audits WHERE {lookup_key} = ?",
            (lookup_value,),
        ).fetchone()
    return _row_to_dict(row), lookup_key


def list_artifacts_for_audit(
    audit_id: str,
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return child artifact rows for ``audit_id`` ordered by ``artifact_sequence``."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM delivered_artifacts
            WHERE audit_id = ?
            ORDER BY artifact_sequence ASC
            """,
            (audit_id,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows if row is not None]


def new_audit_id() -> str:
    """Return a new surrogate parent identifier."""
    return str(uuid.uuid4())


def new_artifact_record_id() -> str:
    """Return a new child row identifier."""
    return str(uuid.uuid4())
