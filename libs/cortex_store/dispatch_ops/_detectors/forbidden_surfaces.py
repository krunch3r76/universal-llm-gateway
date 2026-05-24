"""Forbidden MCP surface detector — plan:document-ingestion-redesign phase-f.

Scans agent-facing docs and live code for retired dispatch surface names.
Runs only when ``include_filesystem=True`` on cortex audit.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from ._shared import _finding

logger = get_logger(__name__)

_FILES_ROOT = Path.home() / "mcp-data" / "files"
_WORKSPACE_ROOT = Path("/mnt/torus/projects/universal-llm-gateway")
# Skip when walking trees; repo ./tmp is ephemeral consult noise.
_SKIP_DIR_PARTS = frozenset({"tmp", "__pycache__", ".git"})

_FORBIDDEN: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bdocument_ocr\b(?!_structured|_directory)"), "document_ocr"),
    (re.compile(r"\bingest_document\b"), "ingest_document"),
    (re.compile(r"\bdocument_ocr_directory\b"), "document_ocr_directory"),
    (re.compile(r"\bdocument_ocr_structured\b"), "document_ocr_structured"),
    (re.compile(r"\bassert_from_chunk\b"), "assert_from_chunk"),
]

# Exempt only detector self-reference, migration infrastructure, and explicit
# provenance disclaimers — not vestigial-rename phrasing ([docs:no-vestigial]).
_EXEMPT_LINE = [
    re.compile(r"^\s*#"),
    re.compile(r"\bnot a rename from\b"),
    re.compile(r"\bprior\s+``"),
    re.compile(r"\b(ingest_chunker|routes/ingest)\b"),
    re.compile(r"\bRemoved \(do not call\)"),
    re.compile(r"\bSupersedes\b"),
    re.compile(r"\bsuperseded by\b", re.I),
    re.compile(r"forbidden_surfaces"),
    re.compile(r"_FORBIDDEN"),
    re.compile(r"Documentation Contract Audit"),
]

_EXEMPT_PATH = (
    "migrations/",
    "test_forbidden_surfaces.py",
    "forbidden_surfaces.py",
    "tools/pipeline_test/",
    "notes/system/transcripts/",
    "notes/system/journals/",
    "notes/system/threads/",
    "/archives/",
    "/tmp/",  # host ephemeral
    "tasks/specs DEPRECATED",
    "agent-skills/_drafts/",
    "agent-skills/_rewrites-pending/",
    "document-ingestion-redesign.md",
)

# Mirror test_forbidden_surfaces workspace roots — not the entire repo tree.
_WORKSPACE_SCAN_ROOTS: list[Path] = [
    _WORKSPACE_ROOT / "services",
    _WORKSPACE_ROOT / "libs",
    _WORKSPACE_ROOT / "config" / "mcp",
    _WORKSPACE_ROOT / ".cursor" / "skills",
]

_CORTEX_SCAN_ROOTS: list[Path] = [
    _FILES_ROOT / "agent-skills",
    _FILES_ROOT / "notes" / "system" / "specs",
    _FILES_ROOT / "notes" / "system" / "decisions",
]

_SCAN_EXTS = frozenset({".py", ".yaml", ".yml", ".md", ".json"})


def _skip_dir(path: Path) -> bool:
    return any(part in _SKIP_DIR_PARTS for part in path.parts)


def _exempt_path(path: Path) -> bool:
    s = str(path)
    return any(x in s for x in _EXEMPT_PATH) or _skip_dir(path)


def _exempt_line(line: str) -> bool:
    return any(p.search(line) for p in _EXEMPT_LINE)


def _scan_file(path: Path) -> list[tuple[int, str, str, str]]:
    hits: list[tuple[int, str, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("forbidden_surfaces: cannot read %s: %s", path, exc)
        return hits
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _exempt_line(line):
            continue
        for pattern, surface in _FORBIDDEN:
            if pattern.search(line):
                snippet = line.strip()[:120]
                hits.append((lineno, surface, snippet, line.rstrip()))
    return hits


def detect_forbidden_surfaces(conn, subject: str | None = None) -> list[dict[str, Any]]:
    """Scan configured roots for retired MCP surface names in live docs/code."""
    del conn, subject  # graph-independent filesystem scan
    findings: list[dict[str, Any]] = []
    scan_roots = list(_WORKSPACE_SCAN_ROOTS) + list(_CORTEX_SCAN_ROOTS)
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if _skip_dir(path):
                continue
            if not path.is_file() or path.suffix not in _SCAN_EXTS:
                continue
            if _exempt_path(path):
                continue
            for lineno, surface, snippet, _full in _scan_file(path):
                rel = path
                try:
                    if path.is_relative_to(_WORKSPACE_ROOT):
                        rel = path.relative_to(_WORKSPACE_ROOT)
                    elif path.is_relative_to(_FILES_ROOT):
                        rel = Path("cortex") / path.relative_to(_FILES_ROOT)
                except ValueError:
                    pass
                audit_id = f"forbidden:{surface}:{rel}:{lineno}"
                findings.append(
                    _finding(
                        "forbidden_surfaces",
                        str(rel),
                        f"{surface} at {rel}:{lineno} — {snippet}",
                        audit_id=audit_id,
                    )
                )
    return findings
