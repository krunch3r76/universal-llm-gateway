"""Agent-facing resume bundle deduplication."""

from __future__ import annotations

import json

import pytest

from agent_bus_store.resume_agent_projection import project_resume_bundle_agent

pytestmark = pytest.mark.offline


def _sample_bundle() -> dict:
    preamble = {
        "stance": "Role:root",
        "facts": "tip_cp=1",
        "next_action": "Run tick once",
    }
    skills = [{"slug": "ide-continuity", "use_line": "Use the `ide-continuity` skill"}]
    mission = {
        "fence_id": "rf-test",
        "preamble": preamble,
        "next_action": "Run tick once",
        "skills_to_use": skills,
        "standing_rules": [{"text": "rule", "assertions": []}],
        "house_unread": [{"turn": 1, "subject": "INFO", "body": "dup"}],
        "handoff": {
            "steps": [
                {"step": 1, "detail": "read mission.next_action tail"},
                {"step": 2, "detail": "repeat mission.next_action again"},
            ]
        },
    }
    return {
        "bundle_version": "resume-bundle-v2",
        "preamble": preamble,
        "mission": mission,
        "skills_to_use": skills,
        "tail": {"next_action": "Run tick once"},
        "resume_envelope": {
            "tape_verbal": [{"turn_number": 1, "subject": "INFO", "content": "dup"}],
        },
        "fence": {"fence_id": "rf-test", "state": "poured"},
    }


def test_resume_projection_deduplicates_top_level_fields() -> None:
    raw = _sample_bundle()
    before = len(json.dumps(raw, default=str).encode("utf-8"))
    proj = project_resume_bundle_agent(raw)
    after = len(json.dumps(proj, default=str).encode("utf-8"))
    assert after < before
    assert "skills_to_use" not in proj["mission"]
    assert proj["skills_to_use"] == raw["skills_to_use"]
    assert "tail" not in proj
    assert proj["agent_projection"]["full_bundle_sha256"]


def test_canonical_stored_bundle_unchanged_by_projection() -> None:
    raw = _sample_bundle()
    stored = json.dumps(raw, sort_keys=True)
    project_resume_bundle_agent(raw)
    assert json.dumps(raw, sort_keys=True) == stored
