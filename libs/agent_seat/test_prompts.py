"""Tests for birth-prompt loading + system-prompt assembly."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_seat.prompts import (
    assemble_system_prompt,
    build_subagent_preamble,
    load_birth_prompt,
)


@pytest.fixture
def identity_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a sandboxed AGENT_IDENTITY_DIR with fake birth prompts."""
    (tmp_path / "orion-birth.md").write_text("I am Orion, the gatherer.")
    (tmp_path / "oppie-birth.md").write_text("I am Oppie, the adversarial seat.")
    monkeypatch.setenv("AGENT_IDENTITY_DIR", str(tmp_path))
    # Clear lru_cache so test-local content is picked up.
    from agent_seat.prompts import _read_identity_file

    _read_identity_file.cache_clear()
    return tmp_path


def test_load_birth_prompt_reads_file(identity_dir: Path) -> None:
    assert load_birth_prompt("orion") == "I am Orion, the gatherer."
    assert load_birth_prompt("oppie") == "I am Oppie, the adversarial seat."


def test_load_birth_prompt_unknown_agent_raises(identity_dir: Path) -> None:
    with pytest.raises(ValueError, match="Unknown agent"):
        load_birth_prompt("phantom")


def test_load_birth_prompt_missing_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_IDENTITY_DIR", raising=False)
    with pytest.raises(ValueError, match="AGENT_IDENTITY_DIR is not set"):
        load_birth_prompt("orion")


def test_load_birth_prompt_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_IDENTITY_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        load_birth_prompt("orion")


def test_subagent_preamble_contains_agent_name() -> None:
    text = build_subagent_preamble("oppie")
    assert '"agent": "oppie"' in text  # baked into the observe/assert example
    assert "Cortex" in text


def test_subagent_preamble_includes_quickref_by_default() -> None:
    text = build_subagent_preamble("oppie")
    assert "CORTEX_TOOL_QUICKREF" not in text  # heading text, not the constant name
    assert 'cortex(tool="search"' in text  # quickref is present


def test_subagent_preamble_suppresses_quickref() -> None:
    text = build_subagent_preamble("oppie", include_cortex_quickref=False)
    assert 'cortex(tool="search"' not in text
    # Contribution guidance (non-quickref body) is still present.
    assert "Cortex Contribution" in text
    assert '"agent": "oppie"' in text


def test_assemble_system_prompt_suppresses_quickref(identity_dir: Path) -> None:
    system = assemble_system_prompt("orion", include_cortex_quickref=False)
    assert 'cortex(tool="search"' not in system
    assert "Cortex Contribution" in system


def test_assemble_system_prompt_stacks_sections(identity_dir: Path) -> None:
    system = assemble_system_prompt(
        "orion",
        briefing_card_md="# Boot Briefing\n- 3 todos open",
        continuation_md="## Resuming From: `transcript:abc`\n**Summary**: foo",
        extra_system="## Caller instructions\nBe concise.",
    )
    # Order: birth → preamble → briefing → continuation → extra.
    assert system.index("I am Orion") < system.index("You are a team member")
    assert system.index("You are a team member") < system.index("Boot Briefing")
    assert system.index("Boot Briefing") < system.index("Resuming From")
    assert system.index("Resuming From") < system.index("Caller instructions")


def test_assemble_system_prompt_minimal(identity_dir: Path) -> None:
    system = assemble_system_prompt("orion")
    assert "I am Orion" in system
    assert "You are a team member" in system
    # No briefing → no briefing markers.
    assert "Boot Briefing" not in system
    assert "Resuming From" not in system


def test_assemble_system_prompt_skips_empty_sections(identity_dir: Path) -> None:
    system = assemble_system_prompt(
        "orion",
        briefing_card_md="   ",
        continuation_md="",
        extra_system=None,
    )
    # Birth + preamble only.
    parts = system.split("\n\n---\n\n")
    assert len(parts) == 2
