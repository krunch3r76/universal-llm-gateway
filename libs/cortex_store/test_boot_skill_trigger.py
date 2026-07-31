"""Tests for boot skill trigger projection from on-disk agent-skills files."""

from __future__ import annotations

from cortex_store.routes.boot._skill_trigger import skill_description_text, skill_trigger_text


def test_skill_trigger_from_frontmatter_description(tmp_path, monkeypatch) -> None:
    root = tmp_path / "files"
    skill_dir = root / "agent-skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "sample-skill.md").write_text(
        "---\n"
        "name: sample-skill\n"
        "description: Do the thing when asked. Extra detail here.\n"
        "---\n\n"
        "# Sample\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cortex_store.routes.boot._skill_trigger._FILES_ROOT",
        root,
    )
    row = {
        "id": "agent_skill:sample-skill",
        "name": "sample-skill",
        "description": "Stale entity description should not win",
        "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/sample-skill/SKILL.md",
    }
    assert skill_trigger_text(row) == "Do the thing when asked"


def test_skill_trigger_from_trigger_line(tmp_path, monkeypatch) -> None:
    root = tmp_path / "files"
    skill_dir = root / "agent-skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "dispatch-shape.md").write_text(
        "# Dispatch Shape\n\n**Trigger:** On any cortex MCP call — read first.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cortex_store.routes.boot._skill_trigger._FILES_ROOT",
        root,
    )
    row = {
        "id": "agent_skill:dispatch-shape",
        "name": "dispatch-shape",
        "description": "Stale",
        "source_uri": str(skill_dir / "dispatch-shape.md"),
    }
    assert skill_trigger_text(row) == "On any cortex MCP call — read first"


def test_skill_trigger_falls_back_to_entity_description() -> None:
    row = {
        "id": "agent_skill:missing-file",
        "name": "missing-file",
        "description": "Entity fallback trigger. More text.",
        "source_uri": None,
    }
    assert skill_trigger_text(row) == "Entity fallback trigger"


def test_skill_description_prefers_trigger_short_over_entity() -> None:
    row = {
        "id": "agent_skill:sample-skill",
        "name": "sample-skill",
        "description": "Long entity description. Extra detail here.",
        "trigger_short": "Short curated trigger.",
        "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/sample-skill/SKILL.md",
    }
    assert skill_description_text(row) == "Short curated trigger."


def test_skill_description_falls_back_to_first_sentence() -> None:
    row = {
        "id": "agent_skill:sparse",
        "name": "sparse",
        "description": "Entity fallback trigger. More text.",
        "source_uri": None,
    }
    assert skill_description_text(row) == "Entity fallback trigger"


def test_skill_description_falls_back_to_trigger_short_only_when_no_l1() -> None:
    row = {
        "id": "agent_skill:sparse",
        "name": "sparse",
        "description": "",
        "source_uri": None,
        "trigger_short": "Load when dispatching handoffs.",
    }
    assert skill_description_text(row) == "Load when dispatching handoffs."
