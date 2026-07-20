"""Resolve optional ``session_summary_md_path`` under CORTEX_FILES_ROOT."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..files_path_normalize import normalize_cortex_files_path
from ._shared import _FILES_ROOT

_EXAMPLE_REL = "notes/system/tmp/<session_id>-summary.md"
_EXAMPLE_URI = "cortex://notes/system/tmp/<session_id>-summary.md"
_SUMMARY_EXAMPLES = [_EXAMPLE_REL, _EXAMPLE_URI]


def summary_path_hint(
    *,
    session_id: str | None = None,
    files_root: Path | None = None,
) -> dict[str, str]:
    """Canonical path-param reminder for preflight / teaching 422s."""
    root = (files_root if files_root is not None else _FILES_ROOT).resolve()
    stem = session_id or "<session_id>"
    rel = f"notes/system/tmp/{stem}-summary.md"
    return {
        "prefer": "session_summary_md_path",
        "example_relative": rel,
        "example_uri": f"cortex://{rel}",
        "files_root": str(root),
        "note": (
            "Prefer path params over inline session_summary_md in MCP JSON. "
            "Write the file under CORTEX_FILES_ROOT first, then pass relative "
            "or cortex:// form."
        ),
    }


def _teaching(
    *,
    received: str,
    root: Path,
    hint: str,
) -> dict[str, Any]:
    return {
        "files_root": str(root.resolve()),
        "examples": _SUMMARY_EXAMPLES,
        "hint": hint,
        "summary_path_hint": summary_path_hint(files_root=root),
        "received": received,
    }


def _normalize_summary_path(
    raw: str, root: Path
) -> tuple[str | None, dict[str, Any] | None]:
    """Return ``(rel_path, error)`` — accept relative, cortex://, host absolutes."""
    rel, err = normalize_cortex_files_path(
        raw,
        root,
        field="session_summary_md_path",
        reason_prefix="session_summary_md_path",
        examples=_SUMMARY_EXAMPLES,
    )
    if err is None:
        return rel, None
    return None, {
        **err,
        **_teaching(
            received=raw,
            root=root,
            hint=str(err.get("hint") or ""),
        ),
    }


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

    Path forms accepted: sandbox-relative (``notes/...``), ``cortex://`` /
    ``cortex:``, and absolute host paths under CORTEX_FILES_ROOT (or known
    aliases ``/data/files``, ``/mnt/torus/mcp-data/files``).
    """
    if not session_summary_md_path:
        return session_summary_md, None

    root = files_root if files_root is not None else _FILES_ROOT
    rel, norm_err = _normalize_summary_path(session_summary_md_path, root)
    if norm_err is not None:
        return None, norm_err
    assert rel is not None

    try:
        abs_path = (root / rel).resolve()
        abs_path.relative_to(root.resolve())
        text = abs_path.read_text(encoding="utf-8")
    except ValueError as exc:
        return None, {
            "error": f"session_summary_md_path escapes CORTEX_FILES_ROOT: {exc}",
            "reason": "session_summary_md_path.sandbox_escape",
            "field": "session_summary_md_path",
            "expected": "path resolved under CORTEX_FILES_ROOT",
            **_teaching(
                received=session_summary_md_path,
                root=root,
                hint="Do not use .. or absolute paths outside the sandbox.",
            ),
        }
    except OSError as exc:
        return None, {
            "error": f"Could not read session_summary_md_path: {exc}",
            "reason": "session_summary_md_path.unreadable",
            "field": "session_summary_md_path",
            "expected": f"readable UTF-8 file at {root.resolve() / rel}",
            **_teaching(
                received=session_summary_md_path,
                root=root,
                hint=(
                    f"Resolved under CORTEX_FILES_ROOT to {(root / rel).as_posix()} "
                    f"— file missing or unreadable. Write it there first "
                    f"(host: {root.resolve() / rel}), then retry with relative "
                    "or cortex:// path. Prefer path params over inline "
                    "session_summary_md in MCP JSON."
                ),
            ),
        }
    except UnicodeDecodeError as exc:
        return None, {
            "error": f"session_summary_md_path is not UTF-8: {exc}",
            "reason": "session_summary_md_path.not_utf8",
            "field": "session_summary_md_path",
            "expected": "UTF-8 text",
            "examples": [],
            "hint": "Summary files must be UTF-8 markdown.",
            "received": session_summary_md_path,
        }
    if not text.strip():
        return None, {
            "error": "session_summary_md_path resolved to empty content",
            "reason": "session_summary_md_path.empty",
            "field": "session_summary_md_path",
            "expected": "non-empty markdown",
            "examples": [],
            "hint": "Write a non-empty ## Session Summary body to the file.",
            "received": session_summary_md_path,
        }
    return text, None


__all__ = ["resolve_session_summary_md", "summary_path_hint"]
