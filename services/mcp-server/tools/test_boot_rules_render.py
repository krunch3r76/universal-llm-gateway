"""Tests for Agent Rules section rendering in boot briefing cards."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools._boot_helpers._rules_section import render_rules_section


def test_render_rules_section_ranks_trigger_matched_ahead_of_catalog() -> None:
    rel = {
        "id": "rule:r1",
        "name": "r1",
        "trigger_match_terms": ["alpha"],
    }
    cat = {"id": "rule:r2", "name": "r2", "skill_category": "z"}
    out = "\n".join(render_rules_section([cat, rel], {"alpha"}))
    assert "## Agent Rules" in out
    assert "### Relevant now" in out
    assert out.index("r1") < out.index("r2")


def test_render_rules_section_is_manifest_only() -> None:
    row = {
        "id": "rule:r3",
        "name": "r3",
        "body": "SENTINEL_BODY_TEXT",
        "description": "SENTINEL_BODY_TEXT",
    }
    out = "\n".join(render_rules_section([row], set()))
    assert "SENTINEL_BODY_TEXT" not in out


def test_render_rules_section_surfaces_source_uri_and_digest() -> None:
    row = {
        "id": "rule:r4",
        "name": "r4",
        "source_uri": "workspaces://docs/agent-guides/rules/r4.md",
        "digest": "sha256:abc1230000000000",
    }
    out = "\n".join(render_rules_section([row], set()))
    assert "workspaces://docs/agent-guides/rules/r4.md" in out
    assert "sha256:abc1230000000000" in out
