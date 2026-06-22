"""FS-touching detector: implement-ready assertion vs on-disk dense-spec ground truth.

Catches the over-claim class where ``status(todo, implement_ready, current)`` is
asserted against a dense spec that ``validate_dense_spec`` would reject at
implement-dispatch admission (friction 20198 / assertion 20191).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from implement_admission.dense_spec_schema import (
    DENSE_SPEC_RE,
    dense_spec_sha256,
    validate_dense_spec,
)
from implement_admission.implement_ready import _assertion_inactive

from ...db import query
from .._shared import _FILES_ROOT
from ._shared import _finding

_KIND = "implement_ready_spec_unvalidated"
_IMPLEMENT_READY_PREDICATE_RE = re.compile(
    r"^status\([^,]+,implement_ready,current\)$",
    re.I,
)


def _normalize_predicate(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return "".join(raw.split()).lower()


def _decode_attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_evidence_uris(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _is_implement_ready_assertion(row: dict[str, Any]) -> bool:
    predicate = row.get("predicate_form")
    if predicate is not None and str(predicate).strip():
        return bool(_IMPLEMENT_READY_PREDICATE_RE.match(_normalize_predicate(predicate)))
    claim = row.get("claim") or ""
    return "status(" in claim.lower() and "implement_ready" in claim.lower()


def _resolve_spec_path(attrs: dict[str, Any], evidence_uris: list[str]) -> str | None:
    dense_path = attrs.get("dense_spec_path")
    if dense_path and str(dense_path).strip():
        uri = str(dense_path).strip()
        match = DENSE_SPEC_RE.search(uri)
        return match.group(0) if match else uri.lstrip("/")

    for uri in evidence_uris:
        match = DENSE_SPEC_RE.search(uri)
        if match:
            return match.group(0)
    return None


def _resolve_spec_file(spec_path: str) -> Path | None:
    uri = spec_path.strip()
    for prefix in ("workspaces://", "cortex://", "ws://", "files://"):
        if uri.lower().startswith(prefix):
            uri = uri[len(prefix) :]
    uri = uri.lstrip("/")
    if not uri:
        return None
    candidate = (_FILES_ROOT / uri).resolve()
    root = _FILES_ROOT.resolve()
    if not str(candidate).startswith(str(root) + "/") and candidate != root:
        return None
    return candidate if candidate.is_file() else None


def _read_spec_text(spec_path: str) -> str | None:
    path = _resolve_spec_file(spec_path)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _sha_attested(evidence_uris: list[str], spec_text: str) -> bool:
    token = f"spec_sha256:{dense_spec_sha256(spec_text)}"
    return token in evidence_uris


def detect_implement_ready_spec_unvalidated(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Active implement-ready assertions whose cited dense spec fails validation."""
    sql = """
        SELECT a.id AS assertion_id, a.entity_id, a.claim, a.predicate_form,
               a.evidence_uris, a.valid_until, a.superseded_by,
               e.attributes
        FROM assertions a
        JOIN entities e ON e.id = a.entity_id
        WHERE a.superseded_by IS NULL
          AND e.type = 'todo'
    """
    params: tuple = ()
    if subject:
        sql += " AND a.entity_id = ?"
        params = (subject,)

    now_iso = datetime.now(UTC).isoformat()
    findings: list[dict[str, Any]] = []

    for row in query(conn, sql, params):
        if not _is_implement_ready_assertion(row):
            continue
        if _assertion_inactive(row, now_iso=now_iso):
            continue

        entity_id = row["entity_id"]
        assertion_id = row["assertion_id"]
        attrs = _decode_attributes(row.get("attributes"))
        evidence_uris = _parse_evidence_uris(row.get("evidence_uris"))

        spec_path = _resolve_spec_path(attrs, evidence_uris)
        if spec_path is None:
            findings.append(
                _finding(
                    _KIND,
                    entity_id,
                    f"assertion {assertion_id} on {entity_id} claims implement-ready "
                    "but no dense spec path could be resolved from "
                    "attributes.dense_spec_path or evidence_uris.",
                    audit_id=f"{_KIND}:{assertion_id}",
                )
            )
            continue

        spec_text = _read_spec_text(spec_path)
        if spec_text is None:
            findings.append(
                _finding(
                    _KIND,
                    entity_id,
                    f"assertion {assertion_id} on {entity_id} claims implement-ready "
                    f"but dense spec at {spec_path!r} is missing or unreadable on disk.",
                    audit_id=f"{_KIND}:{assertion_id}",
                )
            )
            continue

        issues: list[str] = []
        verdict = validate_dense_spec(spec_text)
        if not verdict.passed:
            issues.append(
                f"dense spec fails validate_dense_spec ({verdict.code}: {verdict.reason})"
            )
        if not _sha_attested(evidence_uris, spec_text):
            issues.append(
                "evidence_uris lacks matching spec_sha256 token for current file content"
            )

        if not issues:
            continue

        findings.append(
            _finding(
                _KIND,
                entity_id,
                f"assertion {assertion_id} on {entity_id} claims implement-ready "
                f"but cited spec {spec_path!r} is not admission-clean: "
                f"{'; '.join(issues)}.",
                audit_id=f"{_KIND}:{assertion_id}",
            )
        )

    return findings


__all__ = ["detect_implement_ready_spec_unvalidated"]
