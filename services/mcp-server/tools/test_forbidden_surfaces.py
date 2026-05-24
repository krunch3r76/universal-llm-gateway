"""Forbidden MCP surface detector — plan:document-ingestion-redesign phase-f.

Verifies that agent-facing tool help text does not instruct agents to call
MCP surface names that were retired in phase-c of the document ingestion
redesign (ingest_document → extract_document; document_ocr_directory →
extract_directory; document_ocr → extract_document).

``ingest_binary`` is a live private tool under ``tools/local/`` and is
outside this scan. To inventory ``ingest_binary`` mentions, exclude ephemeral
``./tmp`` (consult-run JSON dumps reference the name heavily)::

    rg '\\bingest_binary\\b' services libs config .cursor --glob '!tmp/**'

Help text must not instruct agents to call the *old* public surfaces — use
the new names instead.

Allowlist: migration paths, detector self-reference, ``#`` comment lines,
and explicit provenance disclaimers (e.g. ``not a rename from``). Vestigial
rename phrasing (``Renamed from``, ``formerly known as``, ``RETIRED``) is
not exempt — [docs:no-vestigial].
"""

from __future__ import annotations

import re
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOLS_ROOT = Path(__file__).resolve().parent
_SKIP_DIR_PARTS = frozenset({"tmp", "__pycache__", ".git"})
_INGEST_BINARY_RE = re.compile(r"\bingest_binary\b")

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
        "document_ocr_directory (renamed to extract_directory)",
    ),
    (
        re.compile(r"\bdocument_ocr_structured\b"),
        "document_ocr_structured (renamed to extract_document_structured)",
    ),
]

# Lines that are exempt from the detector even if they match a pattern.
# A line is exempt if it matches any of these regexes.
_EXEMPT_LINE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*#"),
    re.compile(r"\bnot a rename from\b"),
    re.compile(r"\bprior\s+``"),
    re.compile(r"\b(ingest_chunker|routes/ingest)\b"),
]

# Paths (relative to repo root) that are entirely exempt.
_EXEMPT_PATH_SUFFIXES: tuple[str, ...] = (
    "migrations/",
    "test_forbidden_surfaces.py",  # this file itself
    "forbidden_surfaces.py",
)

# Directories to scan (relative to repo root).
_SCAN_ROOTS: list[Path] = [
    _REPO_ROOT / "services",
    _REPO_ROOT / "libs",
    _REPO_ROOT / "config" / "mcp",
    _REPO_ROOT / ".cursor" / "skills",
]

# ingest_binary inventory scope (B2 verification) — same roots, skip ./tmp.
_INGEST_BINARY_SEARCH_ROOTS: list[Path] = [
    _REPO_ROOT / "services",
    _REPO_ROOT / "libs",
    _REPO_ROOT / "config",
    _REPO_ROOT / ".cursor",
]
_EXPECTED_INGEST_BINARY_FILES: frozenset[Path] = frozenset(
    {
        _REPO_ROOT / "services/mcp-server/tools/local/ingest_binary.py",
        _REPO_ROOT / "services/mcp-server/tools/promote_document_to_evidence.py",
        _REPO_ROOT / "services/mcp-server/tools/test_forbidden_surfaces.py",
    }
)


def _skip_dir(path: Path) -> bool:
    return any(part in _SKIP_DIR_PARTS for part in path.parts)


def _is_exempt_path(path: Path) -> bool:
    """True if the file is in an exempt path (migrations, detector self-ref, etc.)."""
    path_str = str(path)
    return any(exempt in path_str for exempt in _EXEMPT_PATH_SUFFIXES) or _skip_dir(
        path
    )


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
            if _skip_dir(path) or _is_exempt_path(path):
                continue
            for lineno, line, desc in _scan_file(path):
                violations.append((path, lineno, line, desc))
        # Also scan YAML / Markdown in config/mcp and .cursor/skills
        if root.name in {"mcp", "skills"}:
            for ext in ("*.yaml", "*.yml", "*.md"):
                for path in root.rglob(ext):
                    if _skip_dir(path) or _is_exempt_path(path):
                        continue
                    for lineno, line, desc in _scan_file(path):
                        violations.append((path, lineno, line, desc))
    return violations


def _collect_ingest_binary_files() -> set[Path]:
    """Paths under live roots that mention ingest_binary; ./tmp excluded."""
    found: set[Path] = set()
    for root in _INGEST_BINARY_SEARCH_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if _skip_dir(path) or not path.is_file():
                continue
            if path.suffix not in {".py", ".md", ".yaml", ".yml", ".json"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _INGEST_BINARY_RE.search(text):
                found.add(path.resolve())
    return found


def test_ingest_binary_reference_inventory_excludes_tmp() -> None:
    """ingest_binary lives in tools/local; stray mentions must not hide in ./tmp."""
    found = _collect_ingest_binary_files()
    assert found == {p.resolve() for p in _EXPECTED_INGEST_BINARY_FILES}


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
