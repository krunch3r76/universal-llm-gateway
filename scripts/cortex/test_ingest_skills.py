"""Offline unit tests for ingest skill source_uri resolution (catalog/Cursor SOT)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
_REPO = _SCRIPTS_CORTEX.parent.parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from _skill_constants import _SUPPRESSED, _WS  # noqa: E402
from _skill_scan import _source_uri  # noqa: E402

_CURSOR_SOT_SLUGS = (
    "build-pipeline",
    "consult-routing",
    "dispatch-shape",
    "git-posture",
    "agent-guidance-writing",
    "architecture-invariants",
    "ulg-architecture",
    "friction-review",
    "refine-pipeline",
    "handoff-packet-authoring",
    "research-article-ingest",
    "debug-with-events",
    "add-mcp-tool",
    "multi-model-review",
)


def _stub_body(slug: str) -> str:
    path = _REPO / ".cursor" / "skills" / slug / "SKILL.md"
    return path.read_text(encoding="utf-8")


@pytest.mark.offline
@pytest.mark.parametrize("slug", _CURSOR_SOT_SLUGS)
def test_source_uri_resolves_to_cursor_sot(slug: str) -> None:
    body = _stub_body(slug)
    assert _source_uri(slug, body, _REPO) == (
        f"{_WS}/.cursor/skills/{slug}/SKILL.md"
    )


@pytest.mark.offline
def test_source_uri_ignores_cross_ref_docs_paths(tmp_path: Path) -> None:
    slug = "consult-routing"
    body = (
        "See universal-llm-gateway/docs/agent-guides/skills/friction-review.md "
        "for related guidance."
    )
    assert _source_uri(slug, body, tmp_path) == (
        f"{_WS}/.cursor/skills/{slug}/SKILL.md"
    )


@pytest.mark.offline
def test_delegate_to_grok_suppressed_lifecycle_unchanged() -> None:
    assert "deprecated" in _SUPPRESSED
