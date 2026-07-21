"""Advisory todo implement-readiness risk detector (warn-only, never blocks)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from implement_admission.dense_spec_schema import validate_dense_spec
from implement_admission.scheme_resolve import resolve_schemed_packet_file

from ...db import query
from ._shared import _finding

_IMPL_INTENT_STATES = ("open", "in_progress")
_ULG_FLOOR_SKILLS = frozenset(
    {"architecture-invariants", "ulg-architecture", "docstring-quality"}
)
_DESIGN_BEARING_EXACT = frozenset(
    {
        "build-pipeline",
        "architecture-handoff-protocol",
        "implementation-plan-workflow",
    }
)
_DESIGN_BEARING_SUBSTR = ("schema", "protocol", "migration")
_HEADING_RE = re.compile(r"^(#{1,6})\s+")
_SOFT_INCOMPLETE_RE = re.compile(r"\b(TBD|UNRESOLVED|DECIDE|TODO)\b", re.I)
_FENCE_RE = re.compile(r"(```|~~~).*?\1", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_SECTION_HEADING_RES = (
    re.compile(r"^#{1,6}\s.*\bproblem\b", re.I),
    re.compile(r"^#{1,6}\s.*(non-?goal|scope exclusion)", re.I),
    re.compile(r"^#{1,6}\s.*(source[- ]of[- ]truth|provenance)", re.I),
    re.compile(r"^#{1,6}\s.*(touch[- ]?point|touchpoint)", re.I),
    re.compile(
        r"^#{1,6}\s.*(bound design|fork table|design decision|resolved fork)", re.I
    ),
    re.compile(r"^#{1,6}\s.*(implementation guidance|implementation steps)", re.I),
    re.compile(r"^#{1,6}\s.*\bacceptance\b", re.I),
    re.compile(r"^#{1,6}\s.*(verification|quality gate)", re.I),
)


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


def _resolve_spec_path(source_uri: str) -> Path | None:
    return resolve_schemed_packet_file(source_uri.strip())


def _read_spec_text(source_uri: str | None) -> str | None:
    if not source_uri or not str(source_uri).strip():
        return None
    path = _resolve_spec_path(str(source_uri))
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _strip_code(text: str) -> str:
    return _INLINE_CODE_RE.sub("", _FENCE_RE.sub("", text))


def _section_has_body(lines: list[str], start: int) -> bool:
    for line in lines[start + 1 :]:
        if _HEADING_RE.match(line):
            break
        if line.strip():
            return True
    return False


def _spec_soft_incomplete(text: str) -> bool:
    visible = _strip_code(text)
    if _SOFT_INCOMPLETE_RE.search(visible):
        return True
    lines = visible.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        for rx in _SECTION_HEADING_RES:
            if rx.match(stripped) and not _section_has_body(lines, idx):
                return True
    return False


def _spec_advisory_flags(
    text: str | None,
    *,
    density_triage: str | None,
    source_resolves: bool,
) -> list[str]:
    if text is None:
        return []
    verdict = validate_dense_spec(text)
    flags: list[str] = []
    if verdict.open_fork_markers > 0:
        flags.append("forks_open")
    if (
        density_triage == "judgment_required"
        and source_resolves
        and not verdict.passed
        and verdict.code == "dense_spec_sections_missing"
    ):
        flags.append("spec_not_dense")
    if _spec_soft_incomplete(text):
        flags.append("spec_soft_incomplete")
    return flags


def _is_design_bearing_skill(slug: str) -> bool:
    if slug in _ULG_FLOOR_SKILLS:
        return False
    if slug in _DESIGN_BEARING_EXACT:
        return True
    lowered = slug.lower()
    return any(token in lowered for token in _DESIGN_BEARING_SUBSTR)


def _design_skills(attrs: dict[str, Any]) -> list[str]:
    raw = attrs.get("required_skills")
    if not isinstance(raw, list):
        return []
    hits = [s for s in raw if isinstance(s, str) and _is_design_bearing_skill(s)]
    return hits


def detect_todo_implement_readiness_risk(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Warn on implement-readiness contradictions; suppression-independent."""
    placeholders = ",".join("?" * len(_IMPL_INTENT_STATES))
    sql = (
        "SELECT id, name, source_uri, attributes FROM entities "
        f"WHERE type = 'todo' AND workflow_state IN ({placeholders})"
    )
    params: tuple = tuple(_IMPL_INTENT_STATES)
    if subject:
        sql += " AND id = ?"
        params = (*params, subject)

    findings: list[dict[str, Any]] = []
    for row in query(conn, sql, params):
        attrs = _decode_attributes(row.get("attributes"))
        triage = attrs.get("density_triage")
        flags: list[str] = []

        source_uri = row.get("source_uri")
        spec_text = _read_spec_text(source_uri)
        source_resolves = spec_text is not None
        flags.extend(
            _spec_advisory_flags(
                spec_text,
                density_triage=triage,
                source_resolves=source_resolves,
            )
        )

        triaged = triage in {"judgment_required", "mechanical", "unknown"}
        source_empty = not source_uri or not str(source_uri).strip()
        if (
            triage == "judgment_required"
            and attrs.get("implement_ready_assertion_id") is None
        ):
            flags.append("stub_not_dense")
        if triaged and source_empty:
            flags.append("stub_not_dense")

        if triage == "mechanical" and _design_skills(attrs):
            flags.append("mechanical_but_design_skills")

        if not flags:
            continue
        detail = (
            f"todo '{row['name']}' implement-readiness risk: "
            f"{', '.join(sorted(set(flags)))}"
        )
        findings.append(_finding("todo_implement_readiness_risk", row["id"], detail))

    return findings


__all__ = ["detect_todo_implement_readiness_risk"]
