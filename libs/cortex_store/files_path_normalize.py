"""Shared CORTEX_FILES_ROOT path normalization and teaching errors."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .handoff_paths import normalize_handoff_source_path

MCP_DATA_FILES_PREFIX = "/mcp-data/files/"
_EXAMPLE_REL = "notes/system/threads/example.md"
_EXAMPLE_URI = "cortex://notes/system/threads/example.md"


def known_cortex_roots(primary: Path) -> list[Path]:
    """Return live root plus known mount aliases for absolute-path canonicalization."""
    roots = [primary.resolve()]
    for alias in (Path("/data/files"), Path("/mnt/torus/mcp-data/files")):
        try:
            resolved = alias.resolve()
        except OSError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots


def rel_from_absolute(abs_path: Path, roots: list[Path]) -> str | None:
    resolved = abs_path.resolve()
    for root in roots:
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    return None


def cortex_files_teaching(
    *,
    received: str,
    root: Path,
    hint: str,
    examples: list[str] | None = None,
) -> dict[str, Any]:
    resolved = root.resolve()
    return {
        "files_root": str(resolved),
        "examples": examples or [_EXAMPLE_REL, _EXAMPLE_URI],
        "hint": hint,
        "received": received,
    }


def _rewrite_doc_shorthand(cleaned: str) -> tuple[str, dict[str, Any] | None]:
    """Rewrite anchored ``/mcp-data/files/`` doc shorthand to a relative tail."""
    if cleaned.startswith(MCP_DATA_FILES_PREFIX):
        return cleaned[len(MCP_DATA_FILES_PREFIX) :], None
    if cleaned.startswith("/mcp-data/"):
        return cleaned, {
            "kind": "invalid_shorthand",
            "hint": (
                "Documentation mount shorthand must use the anchored prefix "
                f"{MCP_DATA_FILES_PREFIX!r} — not bare /mcp-data/…"
            ),
        }
    return cleaned, None


def normalize_cortex_files_path(
    raw: str,
    root: Path,
    *,
    field: str = "path",
    reason_prefix: str | None = None,
    examples: list[str] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Return ``(rel_path, error)`` for paths under CORTEX_FILES_ROOT.

    Accepts sandbox-relative paths, ``cortex://`` URIs, live/alias absolutes,
    and rewrites doc-shorthand ``/mcp-data/files/…`` onto the live root.
    """
    prefix = reason_prefix or field
    cleaned = raw.strip()
    if not cleaned:
        return None, {
            "error": f"{field} is empty after normalization",
            "reason": f"{prefix}.invalid",
            "field": field,
            "expected": "non-empty cortex-relative path",
            **cortex_files_teaching(
                received=raw,
                root=root,
                hint=(
                    f"Pass a non-empty relative path or cortex:// URI under "
                    f"CORTEX_FILES_ROOT ({root.resolve()})."
                ),
                examples=examples,
            ),
        }

    cleaned, shorthand_err = _rewrite_doc_shorthand(cleaned)
    if shorthand_err is not None:
        return None, {
            "error": (
                f"{field} uses invalid documentation shorthand outside "
                f"CORTEX_FILES_ROOT ({root.resolve()})"
            ),
            "reason": f"{prefix}.outside_files_root",
            "field": field,
            "expected": (
                "relative notes/... path, cortex:// URI, or absolute under files root"
            ),
            **cortex_files_teaching(
                received=raw,
                root=root,
                hint=shorthand_err["hint"],
                examples=examples,
            ),
        }

    candidate = Path(cleaned)
    if candidate.is_absolute():
        roots = known_cortex_roots(root)
        rel = rel_from_absolute(candidate, roots)
        if rel is None:
            return None, {
                "error": (
                    f"{field} is an absolute host path outside "
                    f"CORTEX_FILES_ROOT ({root.resolve()})"
                ),
                "reason": f"{prefix}.outside_files_root",
                "field": field,
                "expected": (
                    "relative notes/... path, cortex:// URI, or absolute under files root"
                ),
                **cortex_files_teaching(
                    received=raw,
                    root=root,
                    hint=(
                        "Host absolute paths must resolve under CORTEX_FILES_ROOT. "
                        "Doc shorthand /mcp-data/files/… is rewritten automatically; "
                        "prefer relative or cortex:// form."
                    ),
                    examples=examples,
                ),
            }
        return rel, None

    rel = normalize_handoff_source_path(cleaned)
    if rel is None:
        return None, {
            "error": f"{field} is empty after normalization",
            "reason": f"{prefix}.invalid",
            "field": field,
            "expected": "non-empty cortex-relative path",
            **cortex_files_teaching(
                received=raw,
                root=root,
                hint="Pass a path under CORTEX_FILES_ROOT (relative or cortex://).",
                examples=examples,
            ),
        }
    return rel, None


__all__ = [
    "MCP_DATA_FILES_PREFIX",
    "cortex_files_teaching",
    "known_cortex_roots",
    "normalize_cortex_files_path",
    "rel_from_absolute",
]
