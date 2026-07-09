"""Resolve optional ``session_summary_md_path`` under CORTEX_FILES_ROOT."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..handoff_paths import normalize_handoff_source_path
from ._shared import _FILES_ROOT


def resolve_session_summary_md(
    *,
    session_summary_md: str | None,
    session_summary_md_path: str | None,
    files_root: Path | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Load summary text; **path wins** when both path and inline are set.

    Returns ``(resolved_text, error_dict)``. ``error_dict`` is None on success.
    When neither path nor inline is set, returns ``(None, None)`` so the
    existing required-field validator can reject.
    """
    if not session_summary_md_path:
        return session_summary_md, None

    root = files_root if files_root is not None else _FILES_ROOT
    rel = normalize_handoff_source_path(session_summary_md_path)
    if rel is None:
        return None, {
            "error": "session_summary_md_path is empty after normalization",
            "reason": "session_summary_md_path.invalid",
            "field": "session_summary_md_path",
            "received": session_summary_md_path,
            "expected": "non-empty cortex-relative path",
            "examples": ["notes/system/sessions/summary.md"],
            "hint": "Pass a path under CORTEX_FILES_ROOT.",
        }
    try:
        abs_path = (root / rel).resolve()
        abs_path.relative_to(root.resolve())
        text = abs_path.read_text(encoding="utf-8")
    except ValueError as exc:
        return None, {
            "error": f"session_summary_md_path escapes CORTEX_FILES_ROOT: {exc}",
            "reason": "session_summary_md_path.sandbox_escape",
            "field": "session_summary_md_path",
            "received": session_summary_md_path,
            "expected": "path resolved under CORTEX_FILES_ROOT",
            "examples": ["notes/system/sessions/summary.md"],
            "hint": "Do not use .. or absolute paths outside the sandbox.",
        }
    except OSError as exc:
        return None, {
            "error": f"Could not read session_summary_md_path: {exc}",
            "reason": "session_summary_md_path.unreadable",
            "field": "session_summary_md_path",
            "received": session_summary_md_path,
            "expected": "readable UTF-8 file under cortex files root",
            "examples": ["notes/system/sessions/summary.md"],
            "hint": "Write the summary file before close, or fix the path.",
        }
    except UnicodeDecodeError as exc:
        return None, {
            "error": f"session_summary_md_path is not UTF-8: {exc}",
            "reason": "session_summary_md_path.not_utf8",
            "field": "session_summary_md_path",
            "received": session_summary_md_path,
            "expected": "UTF-8 text",
            "examples": [],
            "hint": "Summary files must be UTF-8 markdown.",
        }
    if not text.strip():
        return None, {
            "error": "session_summary_md_path resolved to empty content",
            "reason": "session_summary_md_path.empty",
            "field": "session_summary_md_path",
            "received": session_summary_md_path,
            "expected": "non-empty markdown",
            "examples": [],
            "hint": "Write a non-empty ## Session Summary body to the file.",
        }
    return text, None


__all__ = ["resolve_session_summary_md"]
