"""Poll contract for the induction Context grid (thread 12829)."""

from __future__ import annotations

import time

import pytest

from claude_bundles import skill_induction_panel as panel
from claude_bundles.chat_context_skills import (
    ChatContextSkillsError,
    LoadedSkillsReport,
    SkillInductionSnapshot,
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
    url = "https://claude.ai/cowork/cse_x"

    def __init__(self, *, liveness: dict | None = None) -> None:
        self.waits: list[int] = []
        self._liveness = liveness or {
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "url": "https://claude.ai/cowork/cse_x",
        }

    async def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)

    async def evaluate(self, _js: str) -> dict:
        return dict(self._liveness)


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
async def test_pre_use_rail_timeout_emits_pre_use_timeout_reason(monkeypatch) -> None:
    """Never saw post-use Skills group → pre_use_timeout (review agent-bus:15599)."""
    pre_use = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=(),
        context_found=True,
        skills_heading_found=False,
        model_label=None,
        selectors=(),
        raw_section_text="",
    )
    emitted: list[dict] = []

    async def scrape(_page):
        return pre_use

    def _emit(**kwargs):
        emitted.append(kwargs)
        return None

    monkeypatch.setattr(panel, "scrape_loaded_skills", scrape)
    monkeypatch.setattr(panel, "emit_skill_fetch_decision", _emit)
    page = _Page()
    with pytest.raises(SkillDeliveryError, match=r"post_use_rail=False"):
        await panel.wait_for_induction_panel(
            page, ["reasoning-posture"], timeout_s=0, interval_ms=1000
        )
    assert page.waits == []
    assert emitted == [
        {
            "ref": "reasoning-posture",
            "decision": "skipped",
            "reason": "pre_use_timeout",
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
async def test_active_page_past_wall_idle_budget_succeeds(monkeypatch) -> None:
    """Streaming past 45s wall-clock still succeeds when slugs land after quiet."""
    clock = {"t": 100.0}

    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])

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
    snapshots = [pre] * 50 + [post]

    async def scrape(_page):
        return snapshots.pop(0)

    monkeypatch.setattr(panel, "scrape_loaded_skills", scrape)
    monkeypatch.setattr(panel, "emit_skill_fetch_decision", lambda **_k: None)

    page = _Page(liveness={"streaming": True, "stop": False, "tool_pause": False})

    async def wait_for_timeout(ms: int) -> None:
        clock["t"] += ms / 1000.0
        page.waits.append(ms)

    page.wait_for_timeout = wait_for_timeout  # type: ignore[method-assign]

    report = await panel.wait_for_induction_panel(
        page, ["reasoning-posture"], timeout_s=45, interval_ms=1000
    )
    assert report.skills == ("reasoning-posture",)
    assert clock["t"] >= 49.0


@pytest.mark.asyncio
async def test_quiet_pre_use_rail_raises_pre_use_timeout_after_idle(
    monkeypatch,
) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
    pre_use = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=(),
        context_found=True,
        skills_heading_found=False,
        model_label=None,
        selectors=(),
        raw_section_text="",
    )
    emitted: list[dict] = []

    async def scrape(_page):
        return pre_use

    def _emit(**kwargs):
        emitted.append(kwargs)
        return None

    monkeypatch.setattr(panel, "scrape_loaded_skills", scrape)
    monkeypatch.setattr(panel, "emit_skill_fetch_decision", _emit)
    page = _Page()

    async def wait_for_timeout(ms: int) -> None:
        clock["t"] += ms / 1000.0
        page.waits.append(ms)

    page.wait_for_timeout = wait_for_timeout  # type: ignore[method-assign]

    with pytest.raises(SkillDeliveryError, match=r"reason=pre_use_timeout"):
        await panel.wait_for_induction_panel(
            page, ["reasoning-posture"], timeout_s=45, interval_ms=1000
        )
    assert clock["t"] >= 45.0
    assert emitted and emitted[0]["reason"] == "pre_use_timeout"


@pytest.mark.asyncio
async def test_on_harvest_receives_liveness_samples(monkeypatch) -> None:
    pre = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=("reasoning-posture",),
        context_found=True,
        skills_heading_found=True,
        model_label=None,
        selectors=(),
        raw_section_text="Skills\nreasoning-posture",
    )
    samples: list[dict] = []

    async def scrape(_page):
        return pre

    async def on_harvest(state: dict) -> None:
        samples.append(state)

    monkeypatch.setattr(panel, "scrape_loaded_skills", scrape)
    monkeypatch.setattr(panel, "emit_skill_fetch_decision", lambda **_k: None)
    page = _Page(liveness={"streaming": True, "tool_pause": True, "stop": False})
    await panel.wait_for_induction_panel(
        page,
        ["reasoning-posture"],
        timeout_s=30,
        on_harvest=on_harvest,
    )
    assert len(samples) == 1
    assert samples[0]["streaming"] is True
    assert samples[0]["tool_pause"] is True
    assert samples[0]["liveness_observed_at"]


@pytest.mark.asyncio
async def test_absent_header_is_not_retried_until_idle_deadline(monkeypatch) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])

    async def scrape(_page):
        raise ChatContextSkillsError(
            "Context list is not on the rail \u2014 scrape refused until it is expanded"
        )

    monkeypatch.setattr(panel, "scrape_loaded_skills", scrape)
    monkeypatch.setattr(panel, "emit_skill_fetch_decision", lambda **_k: None)
    page = _Page()

    async def wait_for_timeout(ms: int) -> None:
        clock["t"] += ms / 1000.0
        page.waits.append(ms)

    page.wait_for_timeout = wait_for_timeout  # type: ignore[method-assign]

    with pytest.raises(ChatContextSkillsError, match="not on the rail"):
        await panel.wait_for_induction_panel(
            page, ["reasoning-posture"], timeout_s=30, interval_ms=1000
        )
    assert clock["t"] >= panel._PRE_MOUNT_MAX_IDLE_S
    assert page.waits


@pytest.mark.asyncio
async def test_not_on_rail_at_submit_polls_until_cowork_rail_mounts(
    monkeypatch,
) -> None:
    absent = ChatContextSkillsError(
        "Context list is not on the rail \u2014 scrape refused until it is expanded"
    )
    mounted = _report("reasoning-posture")
    snapshots: list = [absent, absent, mounted]
    calls = {"n": 0}

    async def scrape(_page):
        calls["n"] += 1
        item = snapshots.pop(0)
        if isinstance(item, ChatContextSkillsError):
            raise item
        return item

    monkeypatch.setattr(panel, "scrape_loaded_skills", scrape)
    monkeypatch.setattr(panel, "emit_skill_fetch_decision", lambda **_k: None)
    page = _Page(
        liveness={
            "streaming": True,
            "stop": False,
            "tool_pause": False,
            "url": "https://claude.ai/cowork/cse_x",
        }
    )
    report = await panel.wait_for_induction_panel(
        page, ["reasoning-posture"], timeout_s=30, interval_ms=1000
    )
    assert report.skills == ("reasoning-posture",)
    assert calls["n"] == 3
    assert page.waits == [1000, 1000]


@pytest.mark.asyncio
async def test_absent_rail_without_badges_fails_closed_after_stream_quiet(
    monkeypatch,
) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])

    async def scrape(_page):
        raise ChatContextSkillsError(
            "Context list is not on the rail \u2014 scrape refused until it is expanded"
        )

    monkeypatch.setattr(panel, "scrape_loaded_skills", scrape)
    monkeypatch.setattr(panel, "emit_skill_fetch_decision", lambda **_k: None)
    page = _Page(
        liveness={
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "url": "https://claude.ai/chat/c1bb5193-9607-4b8b-81ac-5d4aeea0867c",
        }
    )
    page.url = "https://claude.ai/chat/c1bb5193-9607-4b8b-81ac-5d4aeea0867c"

    streaming_ticks = {"left": 1}

    async def evaluate(_js: str) -> dict:
        if streaming_ticks["left"] > 0:
            streaming_ticks["left"] -= 1
            return {
                "streaming": True,
                "stop": False,
                "tool_pause": False,
                "url": page.url,
            }
        return {
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "url": page.url,
        }

    page.evaluate = evaluate  # type: ignore[method-assign]

    async def wait_for_timeout(ms: int) -> None:
        clock["t"] += ms / 1000.0
        page.waits.append(ms)

    page.wait_for_timeout = wait_for_timeout  # type: ignore[method-assign]

    with pytest.raises(ChatContextSkillsError, match="not on the rail"):
        await panel.wait_for_induction_panel(
            page, ["reasoning-posture"], timeout_s=45, interval_ms=1000
        )
    assert clock["t"] < 45.0


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


@pytest.mark.asyncio
async def test_snapshot_forwards_to_scrape_and_rejects_stale_turn1_slug(
    monkeypatch,
) -> None:
    """R3 F2: stale turn-1 slug in panel+caption fails closed when no new slug lands."""
    clock = {"t": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.expand_context_frame",
        lambda _p: (_ for _ in ()).throw(
            ChatContextSkillsError(
                "Context list is not on the rail \u2014 scrape refused until it is expanded"
            )
        ),
    )
    monkeypatch.setattr(panel, "emit_skill_fetch_decision", lambda **_k: None)

    snap = SkillInductionSnapshot(
        panel_slugs=frozenset({"reasoning-posture"}),
        caption_node_ids=frozenset({"item:turn-1"}),
    )
    page = _Page()
    page.url = "https://claude.ai/chat/c1bb5193-9607-4b8b-81ac-5d4aeea0867c"
    streaming_ticks = {"left": 1}

    async def evaluate(js: str) -> dict:
        if "Session activity panel" in js:
            return {
                "session_labels": ["Skills: reasoning-posture"],
                "captions": [
                    {
                        "node_id": "item:turn-1",
                        "text": "Loaded the reasoning-posture skill",
                    }
                ],
            }
        if streaming_ticks["left"] > 0:
            streaming_ticks["left"] -= 1
            return {
                "streaming": True,
                "stop": False,
                "tool_pause": False,
                "url": page.url,
            }
        return {
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "url": page.url,
        }

    page.evaluate = evaluate  # type: ignore[method-assign]

    async def wait_for_timeout(ms: int) -> None:
        clock["t"] += ms / 1000.0
        page.waits.append(ms)

    page.wait_for_timeout = wait_for_timeout  # type: ignore[method-assign]

    with pytest.raises(ChatContextSkillsError, match="not on the rail"):
        await panel.wait_for_induction_panel(
            page,
            ["reasoning-posture"],
            timeout_s=45,
            interval_ms=1000,
            snapshot=snap,
        )


@pytest.mark.asyncio
async def test_snapshot_accepts_new_caption_node_after_submit(monkeypatch) -> None:
    """R3 F2: new caption node absent from pre-submit snapshot satisfies wait."""

    async def refuse_expand(_page) -> bool:
        raise ChatContextSkillsError(
            "Context list is not on the rail \u2014 scrape refused until it is expanded"
        )

    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.expand_context_frame", refuse_expand
    )
    monkeypatch.setattr(panel, "emit_skill_fetch_decision", lambda **_k: None)

    snap = SkillInductionSnapshot(
        panel_slugs=frozenset({"reasoning-posture"}),
        caption_node_ids=frozenset({"item:turn-1"}),
    )
    polls = {"n": 0}
    page = _Page(
        liveness={
            "streaming": True,
            "stop": False,
            "tool_pause": False,
            "url": "https://claude.ai/chat/c1bb5193-9607-4b8b-81ac-5d4aeea0867c",
        }
    )
    page.url = "https://claude.ai/chat/c1bb5193-9607-4b8b-81ac-5d4aeea0867c"

    async def evaluate(js: str) -> dict:
        if "Session activity panel" in js:
            polls["n"] += 1
            captions = [
                {
                    "node_id": "item:turn-1",
                    "text": "Loaded the reasoning-posture skill",
                }
            ]
            if polls["n"] >= 2:
                captions.append(
                    {
                        "node_id": "item:turn-2",
                        "text": "Loaded the reasoning-posture skill",
                    }
                )
            return {
                "session_labels": ["Skills: reasoning-posture"],
                "captions": captions,
            }
        return dict(page._liveness)

    page.evaluate = evaluate  # type: ignore[method-assign]
    report = await panel.wait_for_induction_panel(
        page,
        ["reasoning-posture"],
        timeout_s=30,
        interval_ms=1000,
        snapshot=snap,
    )
    assert report.skills == ("reasoning-posture",)
    assert polls["n"] == 2
    assert page.waits == [1000]
