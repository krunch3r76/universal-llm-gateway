"""Pipeline id rag-context must not appear outside archive / legacy-compat trees."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]
_PIPELINE_ID = re.compile(r"""['"]rag-context['"]""")

_SKIP_DIR_NAMES = frozenset(
    {".git", "__pycache__", "node_modules", ".venv", "venv", "tmp", ".pytest_cache"}
)
_ARCHIVE_TREE_PREFIXES = (
    "agent-surface/",
    "docs/",
    ".cursor/",
    "cursor-plugins/",
    "config/",
)
_LEGACY_COMPAT = frozenset(
    {
        "services/mcp-server/tools/_rag_relay_options.py",
        "services/mcp-server/tools/test_rag_search_relay.py",
        "services/mcp-server/tools/test_rag_relay_options_match.py",
        "services/universal-stargate/systems/pipeline/core/step_controls.py",
        "services/universal-stargate/systems/pipeline/core/executor/preparation.py",
        "services/universal-stargate/systems/pipeline/core/handlers/pipeline_call.py",
        "services/universal-stargate/systems/pipeline/core/test_pipeline_call.py",
        "services/universal-stargate/tests/test_pipeline_model_id_routing.py",
    }
)
_SCAN_SUFFIXES = frozenset({".py", ".yaml", ".yml", ".md", ".mdc", ".sh"})


def _scan_file(rel: str, path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    hits: list[str] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if _PIPELINE_ID.search(line):
            hits.append(f"{rel}:{line_no}:{line.strip()}")
    return hits


def test_no_rag_context_pipeline_id_outside_archive_trees() -> None:
    """Break: reintroduced caller still posts model rag-context after v1 delete."""
    violations: list[str] = []
    for path in _REPO.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(_REPO).as_posix()
        if any(part in _SKIP_DIR_NAMES for part in rel.split("/")):
            continue
        if any(rel.startswith(prefix) for prefix in _ARCHIVE_TREE_PREFIXES):
            continue
        if "/archive/" in rel:
            continue
        if rel in _LEGACY_COMPAT:
            continue
        if path.suffix not in _SCAN_SUFFIXES:
            continue
        violations.extend(_scan_file(rel, path))
    assert not violations, "rag-context pipeline id literals:\n" + "\n".join(violations)
