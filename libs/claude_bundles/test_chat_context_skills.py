"""Offline tests for Context→Skills section parse (arc 6895 code gate)."""

from __future__ import annotations

import pytest

from claude_bundles.chat_context_skills import (
    LoadedSkillsReport,
    parse_skills_from_context_section,
)

pytestmark = pytest.mark.offline


def test_parse_fable_probe_context_section_verbatim() -> None:
    """Live Fable CSE section text (2026-08-07) — only reasoning-posture bound."""
    section = """Progress
See task progress for longer tasks.
Outputs
View and open files created during this task.
Context
Skills
reasoning-posture
"""
    assert parse_skills_from_context_section(section) == ("reasoning-posture",)


def test_parse_both_required_skills() -> None:
    section = """Context
Skills
reasoning-posture
frontier-reasoning-discipline
"""
    assert parse_skills_from_context_section(section) == (
        "reasoning-posture",
        "frontier-reasoning-discipline",
    )


def test_parse_stops_at_next_section() -> None:
    section = """Context
Skills
reasoning-posture
Files
some-file.md
"""
    assert parse_skills_from_context_section(section) == ("reasoning-posture",)


def test_parse_empty_skills_group() -> None:
    section = """Context
Skills
"""
    assert parse_skills_from_context_section(section) == ()


def test_parse_no_skills_heading() -> None:
    assert parse_skills_from_context_section("Context\nFiles\nfoo") == ()


def test_missing_required() -> None:
    report = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=("reasoning-posture",),
        context_found=True,
        skills_heading_found=True,
        model_label="Fable 5 High",
        selectors=(),
        raw_section_text="Skills\nreasoning-posture",
    )
    assert report.missing(["reasoning-posture", "frontier-reasoning-discipline"]) == (
        "frontier-reasoning-discipline",
    )
