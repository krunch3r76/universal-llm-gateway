"""Poll contract for the induction Context grid (thread 12829)."""

from __future__ import annotations

import pytest

from claude_bundles import skill_induction_panel as panel
from claude_bundles.chat_context_skills import (
    ChatContextSkillsError,
    LoadedSkillsReport,
)
from claude_bundles.cowork_skill_delivery import SkillDeliveryError

pytestmark = pytest.mark.offline


def _report(*skills: str) -> LoadedSkillsReport:
    return LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=skills,
        context_found=True,
        skills_heading_found=True,
        model_label=None,
        selectors=(),
        raw_section_text="",
    )


class _Page:
    def __init__(self) -> None:
        self.waits: list[int] = []

    async def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


@pytest.mark.asyncio
async def test_empty_open_grid_is_reread_until_the_slug_appears(monkeypatch) -> None:
    snapshots = [_report(), _report("reasoning-posture")]
    emitted: list[dict] = []

    async def scrape(_page):
        return snapshots.pop(0)

    def _emit(**kwargs):
        emitted.append(kwargs)
        return None

    monkeypatch.setattr(panel, "scrape_loaded_skills", scrape)
    monkeypatch.setattr(panel, "emit_skill_fetch_decision", _emit)
    page = _Page()
    report = await panel.wait_for_induction_panel(
        page, ["reasoning-posture"], timeout_s=30, interval_ms=1000
    )
    assert report.skills == ("reasoning-posture",)
    assert page.waits == [1000]
    assert emitted == [
        {
            "ref": "reasoning-posture",
            "decision": "in_context",
            "reason": "",
            "required": ["reasoning-posture"],
            "observed": ["reasoning-posture"],
        }
    ]


@pytest.mark.asyncio
async def test_open_grid_that_stays_empty_fails_closed(monkeypatch) -> None:
    emitted: list[dict] = []

    async def scrape(_page):
        return _report()

    def _emit(**kwargs):
        emitted.append(kwargs)
        return None

    monkeypatch.setattr(panel, "scrape_loaded_skills", scrape)
    monkeypatch.setattr(panel, "emit_skill_fetch_decision", _emit)
    page = _Page()
    with pytest.raises(SkillDeliveryError, match="open_grid_reads=1"):
        await panel.wait_for_induction_panel(
            page, ["reasoning-posture"], timeout_s=0, interval_ms=1000
        )
    assert page.waits == []
    assert emitted == [
        {
            "ref": "reasoning-posture",
            "decision": "skipped",
            "reason": "missing_required_slugs",
            "required": ["reasoning-posture"],
            "observed": [],
        }
    ]


@pytest.mark.asyncio
async def test_pre_use_rail_is_retried_until_post_use_population(monkeypatch) -> None:
    """Pre-use empty Context (no Skills heading) keeps polling until slugs land."""
    pre = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=(),
        context_found=True,
        skills_heading_found=False,
        model_label=None,
        selectors=(),
        raw_section_text="",
    )
    post = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=("reasoning-posture",),
        context_found=True,
        skills_heading_found=True,
        model_label=None,
        selectors=(),
        raw_section_text="Skills\nreasoning-posture",
    )
    snapshots = [pre, post]

    async def scrape(_page):
        return snapshots.pop(0)

    monkeypatch.setattr(panel, "scrape_loaded_skills", scrape)
    monkeypatch.setattr(panel, "emit_skill_fetch_decision", lambda **_k: None)
    page = _Page()
    report = await panel.wait_for_induction_panel(
        page, ["reasoning-posture"], timeout_s=30, interval_ms=1000
    )
    assert report.skills == ("reasoning-posture",)
    assert page.waits == [1000]


@pytest.mark.asyncio
async def test_collapsed_header_is_not_retried(monkeypatch) -> None:
    async def scrape(_page):
        raise ChatContextSkillsError(
            "Context list is still collapsed after open — scrape refused"
        )

    emitted: list[dict] = []

    def _emit(**kwargs):
        emitted.append(kwargs)
        return None

    monkeypatch.setattr(panel, "scrape_loaded_skills", scrape)
    monkeypatch.setattr(panel, "emit_skill_fetch_decision", _emit)
    page = _Page()
    with pytest.raises(ChatContextSkillsError, match="still collapsed"):
        await panel.wait_for_induction_panel(
            page, ["reasoning-posture"], timeout_s=30, interval_ms=1000
        )
    assert page.waits == []
    assert emitted == []
