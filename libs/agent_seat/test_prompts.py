"""Tests for system-prompt assembly.

Birth-prompt loading was retired in Phase 7 of the agent-naming cleanup arc
(files absent from $AGENT_IDENTITY_DIR; loader was failing on every call).
Tests for load_birth_prompt / identity_dir fixture are removed.
The assemble_system_prompt stack is now preamble-first.
"""

from __future__ import annotations

from agent_seat.prompts import (
    assemble_system_prompt,
    build_subagent_preamble,
)


def test_subagent_preamble_contains_agent_name() -> None:
    text = build_subagent_preamble("skeptic")
    assert '"agent": "skeptic"' in text  # baked into the observe/assert example
    assert "Cortex" in text


def test_subagent_preamble_includes_quickref_by_default() -> None:
    text = build_subagent_preamble("skeptic")
    assert "CORTEX_TOOL_QUICKREF" not in text  # heading text, not the constant name
    assert 'cortex(tool="search"' in text  # quickref is present


def test_subagent_preamble_suppresses_quickref() -> None:
    text = build_subagent_preamble("skeptic", include_cortex_quickref=False)
    assert 'cortex(tool="search"' not in text
    # Contribution guidance (non-quickref body) is still present.
    assert "Cortex Contribution" in text
    assert '"agent": "skeptic"' in text


def test_assemble_system_prompt_suppresses_quickref() -> None:
    system = assemble_system_prompt("gatherer", include_cortex_quickref=False)
    assert 'cortex(tool="search"' not in system
    assert "Cortex Contribution" in system


def test_assemble_system_prompt_stacks_sections() -> None:
    system = assemble_system_prompt(
        "gatherer",
        briefing_card_md="# Boot Briefing\n- 3 todos open",
        continuation_md="## Resuming From: `transcript:abc`\n**Summary**: foo",
        extra_system="## Caller instructions\nBe concise.",
    )
    # Order: preamble → briefing → continuation → extra.
    assert system.index("You are a team member") < system.index("Boot Briefing")
    assert system.index("Boot Briefing") < system.index("Resuming From")
    assert system.index("Resuming From") < system.index("Caller instructions")


def test_assemble_system_prompt_minimal() -> None:
    system = assemble_system_prompt("gatherer")
    assert "You are a team member" in system
    # No briefing → no briefing markers.
    assert "Boot Briefing" not in system
    assert "Resuming From" not in system


def test_assemble_system_prompt_skips_empty_sections() -> None:
    system = assemble_system_prompt(
        "gatherer",
        briefing_card_md="   ",
        continuation_md="",
        extra_system=None,
    )
    # Preamble only.
    parts = system.split("\n\n---\n\n")
    assert len(parts) == 1
