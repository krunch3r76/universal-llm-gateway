"""T0–T3 endeavor birth audit detectors (F-B5 / F-M5)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from ..db import query
from ..type_taxonomy import MATTER_SPECIES
from .constants import ACK_ATTR, COWORK_PROJECT_ATTR
from .events import cortex_endeavor_audit_finding
from .read_models import (
    _attrs,
    birth_missing_pointers,
    endeavor_host,
)


def _cowork_project_stale(value: Any) -> str | None:
    """Return stale reason if present-but-invalid; None if absent or valid UUID."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return "empty"
    try:
        uuid.UUID(value.strip())
    except ValueError:
        return "invalid_uuid"
    return None


def _ack_state(attrs: dict[str, Any], missing: list[str]) -> str:
    if not missing:
        return "none"
    if attrs.get(ACK_ATTR):
        return "acknowledged-unrepaired"
    return "none"


def detect_endeavor_birth_audit(
    conn: sqlite3.Connection,
    subject: str | None = None,
) -> list[dict[str, Any]]:
    """Return T0–T3 findings with bound item schema."""
    sql = (
        "SELECT id, type, attributes FROM entities "
        "WHERE type IN ({})"
    ).format(",".join("?" * len(MATTER_SPECIES)))
    params: tuple[Any, ...] = tuple(MATTER_SPECIES)
    if subject:
        sql += " AND id = ?"
        params = (*params, subject)
    rows = query(conn, sql, params)
    findings: list[dict[str, Any]] = []
    for row in rows:
        attrs = _attrs(row)
        if not endeavor_host(attrs):
            continue
        host_id = str(row["id"])
        missing = birth_missing_pointers(
            conn,
            entity_type=str(row["type"]),
            attrs=attrs,
            host_id=host_id,
        )
        if not missing:
            continue
        resume_blocking = any(k in {"ring_thread", "endeavor_charter_uri"} for k in missing)
        item = {
            "tier": "T1",
            "host": host_id,
            "missing": missing,
            "resume_blocking": resume_blocking,
            "ack_state": _ack_state(attrs, missing),
            "repair_action": "set_host_resume_keys",
            "stale_pointer": None,
        }
        cortex_endeavor_audit_finding(
            host=host_id,
            tier="T1",
            missing=missing,
            resume_blocking=resume_blocking,
        )
        findings.append(
            {
                "kind": "endeavor_birth_audit_finding",
                "subject": host_id,
                "severity": "warning",
                "detail": json.dumps(item),
                "audit_id": f"endeavor-birth:{host_id}",
            }
        )
    return findings


def detect_endeavor_legacy_thread_keys(
    conn: sqlite3.Connection,
    subject: str | None = None,
) -> list[dict[str, Any]]:
    from .constants import LEGACY_THREAD_KEYS

    sql = "SELECT id, attributes FROM entities"
    params: tuple[Any, ...] = ()
    if subject:
        sql += " WHERE id = ?"
        params = (subject,)
    findings: list[dict[str, Any]] = []
    for row in query(conn, sql, params):
        attrs = _attrs(row)
        legacy = [k for k in LEGACY_THREAD_KEYS if k in attrs]
        if not legacy:
            continue
        host_id = str(row["id"])
        findings.append(
            {
                "kind": "endeavor_legacy_thread_key",
                "subject": host_id,
                "severity": "warning",
                "detail": f"legacy keys present: {legacy}",
                "audit_id": f"endeavor-legacy-thread:{host_id}",
            }
        )
    return findings


def detect_endeavor_cowork_project_stale(
    conn: sqlite3.Connection,
    subject: str | None = None,
) -> list[dict[str, Any]]:
    """T3: present-but-invalid cowork_project UUID. Absence is never a finding."""
    sql = (
        "SELECT id, type, attributes FROM entities "
        "WHERE type IN ({})"
    ).format(",".join("?" * len(MATTER_SPECIES)))
    params: tuple[Any, ...] = tuple(MATTER_SPECIES)
    if subject:
        sql += " AND id = ?"
        params = (*params, subject)
    findings: list[dict[str, Any]] = []
    for row in query(conn, sql, params):
        attrs = _attrs(row)
        if not endeavor_host(attrs):
            continue
        if COWORK_PROJECT_ATTR not in attrs:
            continue
        reason = _cowork_project_stale(attrs.get(COWORK_PROJECT_ATTR))
        if reason is None:
            continue
        host_id = str(row["id"])
        item = {
            "tier": "T3",
            "host": host_id,
            "missing": [],
            "resume_blocking": False,
            "ack_state": "none",
            "repair_action": "repoint_or_clear_cowork_project",
            "stale_pointer": COWORK_PROJECT_ATTR,
            "stale_reason": reason,
        }
        cortex_endeavor_audit_finding(
            host=host_id,
            tier="T3",
            missing=[],
            resume_blocking=False,
        )
        findings.append(
            {
                "kind": "endeavor_cowork_project_stale",
                "subject": host_id,
                "severity": "warning",
                "detail": json.dumps(item),
                "audit_id": f"endeavor-cowork-project-stale:{host_id}",
            }
        )
    return findings
