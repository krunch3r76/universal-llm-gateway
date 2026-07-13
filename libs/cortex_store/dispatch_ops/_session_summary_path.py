"""Resolve optional ``session_summary_md_path`` under CORTEX_FILES_ROOT."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..handoff_paths import normalize_handoff_source_path
from ._shared import _FILES_ROOT

_EXAMPLE_REL = "notes/system/tmp/<session_id>-summary.md"
_EXAMPLE_URI = "cortex://notes/system/tmp/<session_id>-summary.md"


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
        "examples": [_EXAMPLE_REL, _EXAMPLE_URI],
        "hint": hint,
        "summary_path_hint": summary_path_hint(files_root=root),
        "received": received,
    }


def _known_cortex_roots(primary: Path) -> list[Path]:
    roots = [primary.resolve()]
    for alias in (Path("/data/files"), Path("/mnt/torus/mcp-data/files")):
        try:
            resolved = alias.resolve()
        except OSError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _rel_from_absolute(abs_path: Path, roots: list[Path]) -> str | None:
    resolved = abs_path.resolve()
    for root in roots:
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    return None


def _normalize_summary_path(
    raw: str, root: Path
) -> tuple[str | None, dict[str, Any] | None]:
    """Return ``(rel_path, error)`` — accept relative, cortex://, host absolutes."""
    cleaned = raw.strip()
    if not cleaned:
        return None, {
            "error": "session_summary_md_path is empty after normalization",
            "reason": "session_summary_md_path.invalid",
            "field": "session_summary_md_path",
            "expected": "non-empty cortex-relative path",
            **_teaching(
                received=raw,
                root=root,
                hint="Pass a non-empty relative path or cortex:// URI under CORTEX_FILES_ROOT.",
            ),
        }

    roots = _known_cortex_roots(root)
    candidate = Path(cleaned)
    if candidate.is_absolute():
        rel = _rel_from_absolute(candidate, roots)
        if rel is None:
            return None, {
                "error": (
                    "session_summary_md_path is an absolute host path outside "
                    f"CORTEX_FILES_ROOT ({root.resolve()})"
                ),
                "reason": "session_summary_md_path.outside_files_root",
                "field": "session_summary_md_path",
                "expected": "relative notes/... path, cortex:// URI, or absolute under files root",
                **_teaching(
                    received=raw,
                    root=root,
                    hint=(
                        "Host absolute paths must resolve under CORTEX_FILES_ROOT. "
                        "Common footgun: writing under mcp-data/notes/ instead of "
                        f"{root.resolve()}/notes/. Pass relative or cortex:// form."
                    ),
                ),
            }
        return rel, None

    rel = normalize_handoff_source_path(cleaned)
    if rel is None:
        return None, {
            "error": "session_summary_md_path is empty after normalization",
            "reason": "session_summary_md_path.invalid",
            "field": "session_summary_md_path",
            "expected": "non-empty cortex-relative path",
            **_teaching(
                received=raw,
                root=root,
                hint="Pass a path under CORTEX_FILES_ROOT (relative or cortex://).",
            ),
        }
    return rel, None


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
