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
        "preamble": dict(preamble),
        "next_action": "Different mission action",
        "skills_to_use": skills,
        "standing_rules": [{"text": "rule", "assertions": []}],
        "house_unread": [{"turn": 1, "subject": "INFO", "body": "house-only"}],
    }
    return {
        "bundle_version": "resume-bundle-v2",
        "preamble": preamble,
        "mission": mission,
        "skills_to_use": skills,
        "tail": {"next_action": "Run tick once", "extra": "keep-me"},
        "resume_envelope": {
            "tape_verbal": [{"turn_number": 2, "subject": "tape", "content": "other"}],
        },
        "fence": {"fence_id": "rf-test", "state": "poured"},
    }


def test_distinct_next_action_preserved() -> None:
    proj = project_resume_bundle_agent(_sample_bundle())
    assert proj["mission"]["next_action"] == "Different mission action"
    assert proj["preamble"]["next_action"] == "Run tick once"


def test_tail_keeps_non_duplicate_fields() -> None:
    proj = project_resume_bundle_agent(_sample_bundle())
    assert proj["tail"]["extra"] == "keep-me"
    assert "next_action" not in proj["tail"]


def test_house_unread_not_dropped_against_tape() -> None:
    proj = project_resume_bundle_agent(_sample_bundle())
    assert proj["mission"]["house_unread"][0]["body"] == "house-only"


def test_canonical_stored_bundle_unchanged_by_projection() -> None:
    raw = _sample_bundle()
    stored = json.dumps(raw, sort_keys=True)
    project_resume_bundle_agent(raw)
    assert json.dumps(raw, sort_keys=True) == stored


def test_error_bundle_passthrough() -> None:
    err = {"error": "no_tip_checkpoint", "reason": "resume_fence.no_tip_checkpoint"}
    assert project_resume_bundle_agent(err) == err
