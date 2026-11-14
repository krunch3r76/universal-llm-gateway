"""Offline tests for Context→Skills section parse (arc 6895 code gate)."""

from __future__ import annotations

import pytest

from claude_bundles.chat_context_disclosure import disclosure_is_expanded
from claude_bundles.chat_context_skills import (
    ChatContextSkillsError,
    LoadedSkillsReport,
    parse_loaded_skills_from_turn_badges,
    parse_skills_from_context_section,
    require_chat_surface,
    scrape_chat_turn_loaded_skills,
    scrape_loaded_skills,
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


class _DisclosurePage:
    url = "https://claude.ai/cowork/cse_x"

    def __init__(self, *, reads: list[dict], click_result: dict | None = None) -> None:
        self.reads = list(reads)
        self.click_result = click_result or {
            "found": True,
            "clicked": True,
            "aria_expanded": None,
            "list_visible": False,
        }
        self.evaluate_calls: list[dict] = []
        self.timeouts: list[int] = []

    async def evaluate(self, _js: str, spec: dict) -> dict:
        self.evaluate_calls.append(dict(spec))
        if spec.get("click"):
            return dict(self.click_result)
        if not self.reads:
            return {"found": False, "aria_expanded": None, "list_visible": False, "clicked": False}
        return dict(self.reads.pop(0))

    async def wait_for_timeout(self, ms: int) -> None:
        self.timeouts.append(ms)


@pytest.mark.asyncio
async def test_expand_context_frame_open_header_does_not_click() -> None:
    from claude_bundles.chat_context_disclosure import expand_context_frame

    open_state = {
        "found": True,
        "aria_expanded": "true",
        "list_visible": False,
        "clicked": False,
    }
    page = _DisclosurePage(reads=[open_state, open_state])
    assert await expand_context_frame(page) is True
    assert all(not c.get("click") for c in page.evaluate_calls)
    assert page.timeouts == []


@pytest.mark.asyncio
async def test_expand_context_frame_collapsed_header_clicks_once() -> None:
    from claude_bundles.chat_context_disclosure import expand_context_frame

    collapsed = {
        "found": True,
        "aria_expanded": "false",
        "list_visible": False,
        "clicked": False,
    }
    opened = {
        "found": True,
        "aria_expanded": "true",
        "list_visible": True,
        "clicked": False,
    }
    page = _DisclosurePage(reads=[collapsed, collapsed, opened])
    assert await expand_context_frame(page) is True
    click_calls = [c for c in page.evaluate_calls if c.get("click")]
    assert len(click_calls) == 1
    assert page.timeouts == [400]


@pytest.mark.asyncio
async def test_expand_context_frame_stays_collapsed_after_one_click() -> None:
    from claude_bundles.chat_context_disclosure import expand_context_frame

    collapsed = {
        "found": True,
        "aria_expanded": "false",
        "list_visible": False,
        "clicked": False,
    }
    page = _DisclosurePage(reads=[collapsed, collapsed, collapsed])
    with pytest.raises(ChatContextSkillsError, match="still collapsed"):
        await expand_context_frame(page)
    click_calls = [c for c in page.evaluate_calls if c.get("click")]
    assert len(click_calls) == 1


def test_parse_loaded_skills_from_turn_badges_single_and_multi() -> None:
    single = "Loaded the reasoning-posture skill."
    multi = "Loaded the reasoning-posture and hypothesize-simulate skills"
    list_form = "Loaded the reasoning-posture, hypothesize-simulate skills."
    assert parse_loaded_skills_from_turn_badges(single) == ("reasoning-posture",)
    assert parse_loaded_skills_from_turn_badges(multi) == (
        "reasoning-posture",
        "hypothesize-simulate",
    )
    assert parse_loaded_skills_from_turn_badges(list_form) == (
        "reasoning-posture",
        "hypothesize-simulate",
    )


class _BadgeTurnPage:
    url = "https://claude.ai/chat/c1bb5193-9607-4b8b-81ac-5d4aeea0867c"

    def __init__(self, *, turn_text: str) -> None:
        self._turn_text = turn_text

    async def evaluate(self, js: str) -> str:
        if "assistant-message" in js or "assistant-turn" in js:
            return self._turn_text
        return ""

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


@pytest.mark.asyncio
async def test_scrape_chat_turn_loaded_skills_ignores_user_and_composer_nodes() -> None:
    class _EvalPage:
        url = "https://claude.ai/chat/x"

        async def evaluate(self, js: str) -> str:
            assert "user-message" in js
            return ""

    assert await scrape_chat_turn_loaded_skills(_EvalPage()) == ()


@pytest.mark.asyncio
async def test_scrape_loaded_skills_falls_back_to_assistant_badge_when_rail_absent(
    monkeypatch,
) -> None:
    async def refuse_expand(_page) -> bool:
        raise ChatContextSkillsError(
            "Context list is not on the rail \u2014 scrape refused until it is expanded"
        )

    async def turn_badges(_page) -> tuple[str, ...]:
        return ("reasoning-posture", "hypothesize-simulate")

    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.expand_context_frame", refuse_expand
    )
    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.scrape_chat_turn_loaded_skills",
        turn_badges,
    )
    page = _BadgeTurnPage(turn_text="")
    report = await scrape_loaded_skills(page)
    assert report.skills == ("reasoning-posture", "hypothesize-simulate")
    assert report.context_found is False
    assert report.skills_rail_post_use is True


@pytest.mark.asyncio
async def test_collapsed_rail_does_not_fall_back_to_badges(monkeypatch) -> None:
    async def collapsed(_page) -> bool:
        raise ChatContextSkillsError(
            "Context list is still collapsed after open — scrape refused"
        )

    async def should_not_run(_page) -> tuple[str, ...]:
        raise AssertionError("badge fallback must not run on collapsed rail")

    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.expand_context_frame", collapsed
    )
    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.scrape_chat_turn_loaded_skills",
        should_not_run,
    )
    page = _BadgeTurnPage(turn_text="Loaded the reasoning-posture skill.")
    with pytest.raises(ChatContextSkillsError, match="still collapsed"):
        await scrape_loaded_skills(page)
