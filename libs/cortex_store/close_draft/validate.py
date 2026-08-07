"""Close draft field validation — graph-write rejection and depth rules."""

from __future__ import annotations

from typing import Any

from ..dispatch_ops._session_summary_path import resolve_session_summary_md
from ..dispatch_ops._shared import _AGENT_SLUG_RE, _SESSION_ID_RE
from .constants import ALLOWED_FIELD_KEYS, GRAPH_WRITE_KEYS
from .depth_defaults import default_depth_for_agent


def reject_graph_write_keys(payload: dict[str, Any]) -> dict[str, Any] | None:
    offenders = [k for k in payload if k in GRAPH_WRITE_KEYS]
    if offenders:
        return {
            "reason": "close_draft.graph_write_forbidden",
            "field": offenders[0],
            "detail": (
                f"Graph-write keys forbidden on close draft: {offenders}. "
                "Use cortex (or imprint) for assert/entity/relationship writes."
            ),
            "keys": offenders,
        }
    unknown = [k for k in payload if k not in ALLOWED_FIELD_KEYS]
    if unknown:
        return {
            "reason": "close_draft.unknown_field",
            "field": unknown[0],
            "detail": f"Unknown draft field(s): {unknown}",
            "keys": unknown,
        }
    return None


def validate_stage_args(*, session_id: str, agent: str) -> dict[str, Any] | None:
    if not _SESSION_ID_RE.match(session_id):
        return {
            "reason": "session_id.invalid",
            "detail": f"session_id {session_id!r} invalid",
        }
    if not _AGENT_SLUG_RE.match(agent):
        return {"reason": "agent.invalid", "detail": f"agent {agent!r} invalid"}
    return None


def resolve_draft_paths(fields: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Resolve path-backed fields; return (resolved_fields, field_errors)."""
    resolved = dict(fields)
    errors: list[dict[str, Any]] = []
    path = fields.get("session_summary_md_path")
    if path:
        text, err = resolve_session_summary_md(
            session_summary_md=fields.get("session_summary_md"),
            session_summary_md_path=str(path),
        )
        if err:
            errors.append({**err, "priority": "high", "action": "Fix session_summary_md_path"})
        elif text:
            resolved["session_summary_md"] = text
    tpath = fields.get("transcript_md_path")
    if tpath:
        text, err = resolve_session_summary_md(
            session_summary_md=None,
            session_summary_md_path=str(tpath),
        )
        if err:
            errors.append({**err, "priority": "high", "action": "Fix transcript_md_path"})
        elif text:
            resolved["_transcript_md_resolved"] = text
    return resolved, errors


def coalesce_draft_fields(
    *,
    nested: dict[str, Any] | None,
    flat: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Merge nested ``fields`` with top-level draft aliases.

    Life/web callers often pass ``summary`` / ``session_summary_md`` at the
    request top level. Pydantic would otherwise drop those extras and leave an
    empty draft — check used to PASS, then commit failed with summary got 0.
    Nested ``fields`` wins on key collision. Returns (merged, unknown_keys).
    """
    flat = flat or {}
    nested = nested or {}
    unknown = [k for k in flat if k not in ALLOWED_FIELD_KEYS]
    folded = {k: v for k, v in flat.items() if k in ALLOWED_FIELD_KEYS}
    return {**folded, **nested}, unknown


def depth_cross_field_gaps(fields: dict[str, Any]) -> list[dict[str, str]]:
    depth = str(fields.get("depth") or default_depth_for_agent(""))
    has_transcript_path = bool(fields.get("transcript_md_path"))
    gaps: list[dict[str, str]] = []
    if depth == "verbatim" and not has_transcript_path:
        gaps.append(
            {
                "code": "verbatim.missing_transcript_path",
                "item": "transcript_md_path",
                "priority": "critical",
                "action": "Set transcript_md_path for depth=verbatim",
            }
        )
    if depth != "verbatim" and has_transcript_path:
        gaps.append(
            {
                "code": "transcript_path.depth_mismatch",
                "item": "transcript_md_path",
                "priority": "critical",
                "action": "Remove transcript_md_path or set depth=verbatim",
            }
        )
    handoff = fields.get("handoff") or fields.get("handoff_source_path")
    if handoff and depth == "none":
        gaps.append(
            {
                "code": "handoff.requires_transcript_entity",
                "item": "depth",
                "priority": "critical",
                "action": "Set depth to light or verbatim when handoff present",
            }
        )
    if depth == "none" and (
        fields.get("decisions") or fields.get("entity_ids") or handoff
    ):
        gaps.append(
            {
                "code": "depth.none_with_content",
                "item": "depth",
                "priority": "critical",
                "action": "Use light/verbatim when decisions/entities/handoff exist",
            }
        )
    # Required for commit/session_close — missing must FAIL check (not only
    # too-short-when-present). Empty draft {depth:light} used to PASS.
    summary = fields.get("summary")
    summary_text = str(summary).strip() if summary is not None else ""
    if len(summary_text) < 20:
        gaps.append(
            {
                "code": "summary.too_short",
                "item": "summary",
                "priority": "critical",
                "action": (
                    "Set fields.summary (≥20 chars) via close(op=draft); "
                    "top-level summary is also accepted as an alias"
                ),
            }
        )
    has_summary_md = bool(str(fields.get("session_summary_md") or "").strip())
    has_summary_path = bool(fields.get("session_summary_md_path"))
    if depth != "none" and not has_summary_md and not has_summary_path:
        gaps.append(
            {
                "code": "session_summary.required",
                "item": "session_summary_md",
                "priority": "critical",
                "action": (
                    "Set fields.session_summary_md (or session_summary_md_path) "
                    "via close(op=draft)"
                ),
            }
        )
    return gaps
