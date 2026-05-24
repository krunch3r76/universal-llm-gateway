"""Forbidden MCP surface detector — plan:document-ingestion-redesign phase-f.

Verifies that agent-facing tool help text does not instruct agents to call
MCP surface names that were retired in phase-c of the document ingestion
redesign (ingest_document → extract_document; document_ocr_directory →
extract_directory; document_ocr → extract_document).

``ingest_binary`` is still a live private tool but is also included in the
scan: any help text instructing agents to call the *old* public surfaces
must use the new names instead.

Allowlist: occurrences in `.retired` files, migration comments, and
``# `` comment lines that document historical renames are exempt.  The
detector catches agent-instructing phrases (dispatch call examples in
docstrings) that slip through after a rename.
"""

from __future__ import annotations

import re
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOLS_ROOT = Path(__file__).resolve().parent

# Old names that must not appear as dispatch targets in agent-facing help text.
# Each entry is (pattern, description).
_FORBIDDEN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bdocument_ocr\b(?!_structured|_directory)"),
        "document_ocr (retired; use extract_document)",
    ),
    (
        re.compile(r"\bingest_document\b"),
        "ingest_document (renamed to extract_document in phase-c)",
    ),
    (
        re.compile(r"\bdocument_ocr_directory\b"),
        "document_ocr_directory (renamed to extract_directory in phase-c)",
    ),
]

# Lines that are exempt from the detector even if they match a pattern.
# A line is exempt if it matches any of these regexes.
_EXEMPT_LINE_PATTERNS: list[re.Pattern[str]] = [
    # Inline code comments — historical migration notes
    re.compile(r"^\s*#"),
    # docstring sentences that document a rename
    re.compile(r"\bRenamed from\b"),
    re.compile(r"\b(formerly|previously) (called|named|known as)\b"),
    # "not a rename from" — provenance disclaimer in promote_document_to_evidence
    re.compile(r"\bnot a rename from\b"),
    # documentation references to historical context: "prior ``X``" idiom
    # (RST/Markdown code reference) and the phase-c project tag.
    re.compile(r"\bprior\s+``"),
    re.compile(r"\bphase-c\b"),
    # migration files (ingest_document inside migration docstrings)
    re.compile(r"\b(ingest_chunker|routes/ingest)\b"),
]

# Paths (relative to repo root) that are entirely exempt.
_EXEMPT_PATH_SUFFIXES: tuple[str, ...] = (
    ".retired",
    "migrations/",
    "test_forbidden_surfaces.py",  # this file itself
)

# Directories to scan (relative to repo root).
_SCAN_ROOTS: list[Path] = [
    _REPO_ROOT / "services",
    _REPO_ROOT / "libs",
    _REPO_ROOT / "config" / "mcp",
    _REPO_ROOT / ".cursor" / "skills",
]


def _is_exempt_path(path: Path) -> bool:
    """True if the file is in an exempt path (retired, migrations, etc.)."""
    path_str = str(path)
    return any(exempt in path_str for exempt in _EXEMPT_PATH_SUFFIXES)


def _is_exempt_line(line: str) -> bool:
    """True if the line is a historical/contextual reference, not an instruction."""
    return any(p.search(line) for p in _EXEMPT_LINE_PATTERNS)


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return (lineno, line, pattern_description) for each forbidden hit."""
    hits: list[tuple[int, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("test_forbidden_surfaces: cannot read %s: %s", path, exc)
        return hits

    for lineno, line in enumerate(text.splitlines(), start=1):
        if _is_exempt_line(line):
            continue
        for pattern, description in _FORBIDDEN_PATTERNS:
            if pattern.search(line):
                hits.append((lineno, line.rstrip(), description))
    return hits


def collect_violations() -> list[tuple[Path, int, str, str]]:
    """Scan all roots and collect (file, lineno, line, description) tuples."""
    violations: list[tuple[Path, int, str, str]] = []
    for root in _SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if _is_exempt_path(path):
                continue
            for lineno, line, desc in _scan_file(path):
                violations.append((path, lineno, line, desc))
        # Also scan YAML / Markdown in config/mcp and .cursor/skills
        if root.name in {"mcp", "skills"}:
            for ext in ("*.yaml", "*.yml", "*.md"):
                for path in root.rglob(ext):
                    if _is_exempt_path(path):
                        continue
                    for lineno, line, desc in _scan_file(path):
                        violations.append((path, lineno, line, desc))
    return violations


def test_no_forbidden_dispatch_surfaces() -> None:
    """Agent-facing help text must not reference retired MCP tool names."""
    violations = collect_violations()
    if not violations:
        return

    lines = ["Forbidden MCP surface references found:\n"]
    for path, lineno, line, desc in violations:
        rel = path.relative_to(_REPO_ROOT)
        lines.append(f"  {rel}:{lineno}  [{desc}]")
        lines.append(f"    {line}")
    assert False, "\n".join(lines)
