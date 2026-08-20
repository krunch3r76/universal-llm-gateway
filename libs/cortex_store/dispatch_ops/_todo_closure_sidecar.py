"""Render + persist the standardized todo-closure markdown sidecar.

The closure *summary* lives canonically on the closure assertion (the audit
trail). This module produces the human-readable index counterpart: a markdown
file under ``notes/system/todos/{slug}-closure.md`` in the cortex sandbox,
mirroring the agent-bus thread-sidecar convention
(``notes/system/threads/<thread>-<subject>.md``).

The markdown format is owned here — in cortex-api — so every closure produces
an identically shaped sidecar regardless of which caller (pipeline:todo-close,
operator, future tooling) drives it. Writes funnel through
``durable_io.atomic`` (flock + temp+fsync+replace + retain).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from durable_io.atomic import durable_write_text

from ._shared import _FILES_ROOT

_SIDECAR_SUBDIR = ("notes", "system", "todos")


def closure_sidecar_uri(slug: str) -> str:
    """Canonical cortex:// URI for a todo's closure sidecar."""
    return f"cortex://notes/system/todos/{slug}-closure.md"


def slug_from_todo_id(todo_id: str) -> str:
    """``todo:foo-bar`` → ``foo-bar`` (tolerant of a bare slug)."""
    return todo_id.removeprefix("todo:")


def render_closure_markdown(
    *,
    todo_id: str,
    summary: str,
    evidence: str | None = None,
    reasoning_summary: str | None = None,
    references: list[dict[str, Any]] | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    closed_at: str | None = None,
) -> str:
    """Build the canonical closure-sidecar markdown body."""
    closed_at = closed_at or datetime.now(UTC).isoformat()
    lines = [f"# Closure — {todo_id}", ""]
    meta = [f"**Closed:** {closed_at}"]
    if agent:
        meta.append(f"**Agent:** {agent}")
    if session_id:
        meta.append(f"**Session:** {session_id}")
    lines.append("  \n".join(meta))
    lines += ["", "## Summary", "", summary.strip()]
    if reasoning_summary:
        lines += ["", "## Reasoning", "", reasoning_summary.strip()]
    if evidence:
        lines += ["", "## Evidence", "", evidence.strip()]
    if references:
        lines += ["", "## References", ""]
        for item in references:
            target = item.get("target") if isinstance(item, dict) else None
            if not target:
                continue
            role = item.get("role") or item.get("type_id") or "references"
            note = item.get("evidence")
            entry = f"- `{target}` ({role})"
            if note:
                entry += f" — {note}"
            lines.append(entry)
    return "\n".join(lines).rstrip() + "\n"


def write_closure_sidecar(slug: str, content: str) -> str:
    """Persist the closure sidecar through the flock-serialised durable leaf.

    Returns the absolute path under ``_FILES_ROOT``. Overwrites retain a
    content-store copy so concurrent notes writers cannot silent-clobber.
    """
    path = _FILES_ROOT.joinpath(*_SIDECAR_SUBDIR) / f"{slug}-closure.md"
    durable_write_text(path, content, retain_store_root=_FILES_ROOT)
    return str(path)
