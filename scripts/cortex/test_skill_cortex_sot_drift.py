"""Offline tests for cortex-SOT description drift via projection path."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from _skill_drift import _drifts  # noqa: E402
from _skill_projection import _matches, _projection  # noqa: E402


class _FakeClient:
    def request(self, method: str, path: str, **kwargs):  # noqa: ANN003
        raise AssertionError(f"unexpected network call: {method} {path}")


def test_matches_detects_cortex_sot_description_drift() -> None:
    row = {
        "slug": "sample-skill",
        "frontmatter": {},
        "description": "File SOT description text.",
        "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/sample-skill/SKILL.md",
        "related_skills": [],
    }
    live = {
        "id": "agent_skill:sample-skill",
        "type": "agent_skill",
        "lifecycle": "active",
        "description": "Stale entity description.",
        "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/sample-skill/SKILL.md",
        "attributes": {"applicable_agents": ["*"]},
    }
    ok, reason = _matches(live, _projection(row, live=live))
    assert ok is False
    assert "description" in reason


def test_drifts_includes_cortex_sot_rows_excluding_workspace_scanned() -> None:
    scanned = {
        "workspace-skill": {
            "slug": "workspace-skill",
            "frontmatter": {"description": "Workspace desc"},
            "description": "Workspace desc",
            "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/x/SKILL.md",
            "related_skills": [],
        }
    }
    cortex_sot = {
        "cortex-only": {
            "slug": "cortex-only",
            "frontmatter": {},
            "description": "Cortex file description.",
            "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/cortex-only/SKILL.md",
            "related_skills": [],
        },
        "workspace-skill": {
            "slug": "workspace-skill",
            "frontmatter": {},
            "description": "Would drift if scanned did not win.",
            "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/workspace-skill/SKILL.md",
            "related_skills": [],
        },
    }
    workspace_row = scanned["workspace-skill"]
    workspace_terms = _projection(workspace_row)["attributes"]["trigger_match_terms"]
    live_by_id = {
        "agent_skill:workspace-skill": {
            "id": "agent_skill:workspace-skill",
            "lifecycle": "active",
            "description": "Workspace desc",
            "source_uri": scanned["workspace-skill"]["source_uri"],
            "attributes": {
                "applicable_agents": ["*"],
                "trigger_match_terms": workspace_terms,
            },
        },
        "agent_skill:cortex-only": {
            "id": "agent_skill:cortex-only",
            "lifecycle": "active",
            "description": "Stale entity text.",
            "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/cortex-only/SKILL.md",
            "attributes": {"applicable_agents": ["*"]},
        },
    }
    drifts = _drifts(_FakeClient(), scanned, live_by_id=live_by_id, cortex_sot=cortex_sot)
    joined = "\n".join(drifts)
    assert "agent_skill:cortex-only" in joined
    assert "description" in joined
    assert "agent_skill:workspace-skill" not in joined or "Stale" not in joined
