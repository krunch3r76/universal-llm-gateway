"""Close draft check — preflight + audit detectors + structural gates."""

from __future__ import annotations

from typing import Any

from ..dispatch_ops._session_todo_reconciliation import (
    open_todos_in_entity_ids,
    todo_reconciliation_warning,
)
from ..dispatch_ops.ops_review_gate import (
    _PRE_CLOSE_GATE_KINDS,
    _run_session_audit_or_block,
)
from ..dispatch_ops.ops_session_close import _assemble_transcript_in_memory
from ..session_close_validation import _validate_transcript_structure
from .validate import depth_cross_field_gaps, resolve_draft_paths


def _audit_gaps(
    *,
    session_id: str,
    agent: str,
    entity_ids: list[str],
) -> list[dict[str, str]]:
    outcome = _run_session_audit_or_block(
        session_id=session_id,
        agent=agent,
        entity_ids=entity_ids,
        defer_gaps=None,
    )
    if outcome.get("blocked"):
        return [
            {
                "code": "session_audit.blocked",
                "item": "audit",
                "priority": "critical",
                "action": str(outcome.get("error") or "Remediate audit blockers"),
            }
        ]
    gaps: list[dict[str, str]] = []
    findings = outcome.get("findings") or []
    if isinstance(findings, list):
        for f in findings:
            if not isinstance(f, dict):
                continue
            kind = str(f.get("kind") or f.get("code") or "audit.gap")
            if kind not in _PRE_CLOSE_GATE_KINDS and not kind.startswith("audit."):
                kind = f"audit.{kind}"
            gaps.append(
                {
                    "code": kind,
                    "item": str(f.get("subject") or f.get("entity_id") or "graph"),
                    "priority": str(f.get("severity") or "medium"),
                    "action": str(
                        f.get("remediation")
                        or f.get("message")
                        or "Remediate audit finding"
                    ),
                }
            )
    warning = outcome.get("warning")
    if isinstance(warning, dict) and warning.get("audit_degraded"):
        gaps.append(
            {
                "code": "audit.degraded",
                "item": "audit",
                "priority": "low",
                "action": "Audit degraded — retry check or inspect logs",
            }
        )
    return gaps


def _todo_gaps(entity_ids: list[str] | None) -> list[dict[str, str]]:
    pending = open_todos_in_entity_ids(entity_ids)
    if not pending:
        return []
    slugs = ", ".join(p["todo_id"] for p in pending)
    return [
        {
            "code": "todo_reconciliation.required",
            "item": "entity_ids",
            "priority": "critical",
            "action": (
                f"Drop open todos from entity_ids ({slugs}) or close them via cortex"
            ),
        }
    ]


def _structural_gaps(
    *,
    fields: dict[str, Any],
    session_id: str,
    agent: str,
) -> list[dict[str, str]]:
    summary_md = fields.get("session_summary_md")
    summary = fields.get("summary")
    depth = str(fields.get("depth") or "light")
    if not summary_md or not summary:
        return []
    if depth == "verbatim" and not fields.get("_transcript_md_resolved"):
        return []
    transcript_md = fields.get("_transcript_md_resolved")
    asm = _assemble_transcript_in_memory(
        session_id=session_id,
        agent=agent,
        transcript_jsonl_path=None,
        transcript_md=transcript_md if depth == "verbatim" else None,
        transcript_depth=depth,  # type: ignore[arg-type]
        session_summary_md=str(summary_md),
    )
    if not asm.get("ok"):
        return [
            {
                "code": str(asm.get("reason") or "transcript.assembly_failed"),
                "item": "transcript",
                "priority": "critical",
                "action": str(asm.get("error") or "Fix transcript assembly"),
            }
        ]
    composed = asm.get("composed") or ""
    warnings = _validate_transcript_structure(
        str(composed),
        summary_len=len(str(summary)),
        transcript_depth=depth,  # type: ignore[arg-type]
    )
    gaps: list[dict[str, str]] = []
    for w in warnings:
        gaps.append(
            {
                "code": "structural.summary",
                "item": "session_summary_md",
                "priority": "medium",
                "action": w,
            }
        )
    return gaps


def run_close_check(
    *,
    session_id: str,
    agent: str,
    fields: dict[str, Any],
    revision: int,
) -> dict[str, Any]:
    resolved, path_errors = resolve_draft_paths(fields)
    gaps: list[dict[str, str]] = []
    for err in path_errors:
        gaps.append(
            {
                "code": str(err.get("reason") or "path.invalid"),
                "item": str(err.get("field") or "path"),
                "priority": str(err.get("priority") or "critical"),
                "action": str(err.get("action") or err.get("hint") or "Fix path"),
            }
        )
    gaps.extend(depth_cross_field_gaps(resolved))
    entity_ids = resolved.get("entity_ids") or []
    if isinstance(entity_ids, list):
        gaps.extend(_todo_gaps(entity_ids))
        gaps.extend(
            _audit_gaps(session_id=session_id, agent=agent, entity_ids=entity_ids)
        )
    gaps.extend(
        _structural_gaps(fields=resolved, session_id=session_id, agent=agent)
    )
    status = "PASS" if not gaps else "FAIL"
    narrative = (
        "Draft ready for commit."
        if status == "PASS"
        else f"{len(gaps)} gap(s) require remediation before commit."
    )
    if entity_ids and (warn := todo_reconciliation_warning(open_todos_in_entity_ids(entity_ids))):
        narrative = f"{narrative} {warn}"
    report = {"gaps": gaps}
    return {
        "status": status,
        "report": report,
        "narrative": narrative,
        "checked_revision": revision if status == "PASS" else None,
        "revision": revision,
    }
