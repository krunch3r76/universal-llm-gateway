"""Offline tests for Context→Skills section parse (arc 6895 code gate)."""

from __future__ import annotations

import pytest

from claude_bundles.chat_context_disclosure import disclosure_is_expanded
from claude_bundles.chat_context_skills import (
    ChatContextSkillsError,
    LoadedSkillsReport,
    parse_skills_from_context_section,
    require_chat_surface,
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
consult-posture
"""
    assert parse_skills_from_context_section(section) == (
        "reasoning-posture",
        "consult-posture",
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


def test_pre_use_open_context_without_skills_group() -> None:
    """a:38612 — Skills heading absent before first Use/<slug> is expected."""
    report = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=(),
        context_found=True,
        skills_heading_found=False,
        model_label=None,
        selectors=(),
        raw_section_text="",
    )
    assert report.pre_use_skills_rail is True
    assert report.skills_rail_post_use is False
    assert parse_skills_from_context_section("Context\n") == ()


def test_open_aria_is_expanded_without_a_second_click() -> None:
    assert disclosure_is_expanded("true", list_visible=False) is True
    assert disclosure_is_expanded("TRUE", list_visible=False) is True


def test_closed_aria_stays_collapsed_even_if_text_remains() -> None:
    assert disclosure_is_expanded("false", list_visible=True) is False


def test_missing_aria_follows_list_visibility() -> None:
    assert disclosure_is_expanded(None, list_visible=True) is True
    assert disclosure_is_expanded("", list_visible=False) is False
    assert disclosure_is_expanded("  ", list_visible=False) is False


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
    assert report.missing(["reasoning-posture", "consult-posture"]) == (
        "consult-posture",
    )


class _UrlPage:
    def __init__(self, url: str) -> None:
        self.url = url


def test_local_cowork_session_is_a_compose_surface() -> None:
    url = "https://claude.ai/cowork/local_0e29ba8a-f756-40d5-949c-39e9b7d85007"
    assert require_chat_surface(_UrlPage(url)) == url


def test_cse_cowork_session_stays_a_compose_surface() -> None:
    url = "https://claude.ai/cowork/cse_01ToWejFedjBWFbKXbAxeKLZ"
    assert require_chat_surface(_UrlPage(url)) == url


def test_settings_url_is_not_a_compose_surface() -> None:
    with pytest.raises(ChatContextSkillsError, match="cowork/local_"):
        require_chat_surface(_UrlPage("https://claude.ai/settings/skills"))
