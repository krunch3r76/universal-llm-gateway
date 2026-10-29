"""Unit tests for post-submit Context → Skills receipt (non-gating)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from claude_bundles.chat_context_skills import (
    ChatContextSkillsError,
    LoadedSkillsReport,
)
from claude_bundles.skill_context_receipt import record_post_submit_skills_receipt


@pytest.mark.asyncio
async def test_receipt_records_missing_without_raising(monkeypatch) -> None:
    report = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=("reasoning-posture",),
        context_found=True,
        skills_heading_found=True,
        model_label="Opus",
        selectors=(),
        raw_section_text="Skills\nreasoning-posture\n",
    )
    emitted: list[dict] = []

    async def _scrape(_page):
        return report

    def _emit(**kwargs):
        emitted.append(kwargs)
        return SimpleNamespace(signal="cdp.skill.context_loaded")

    monkeypatch.setattr(
        "claude_bundles.skill_context_receipt.scrape_loaded_skills",
        _scrape,
    )
    monkeypatch.setattr(
        "claude_bundles.skill_context_receipt.emit_skill_context_loaded",
        _emit,
    )
    page = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    out = await record_post_submit_skills_receipt(
        page,
        required=["reasoning-posture", "consult-posture"],
        execution_id="sg-1",
        satellite_execution_id="sat-1",
        settle_ms=0,
    )
    assert out["ok"] is False
    assert out["missing"] == ["consult-posture"]
    assert out["observed"] == ["reasoning-posture"]
    assert emitted and emitted[0]["missing"] == ["consult-posture"]
    assert emitted[0]["ok"] is False


@pytest.mark.asyncio
async def test_receipt_scrape_error_is_soft(monkeypatch) -> None:
    emitted: list[dict] = []

    async def _boom(_page):
        raise RuntimeError("panel absent")

    def _emit(**kwargs):
        emitted.append(kwargs)
        return None

    monkeypatch.setattr(
        "claude_bundles.skill_context_receipt.scrape_loaded_skills",
        _boom,
    )
    monkeypatch.setattr(
        "claude_bundles.skill_context_receipt.emit_skill_context_loaded",
        _emit,
    )
    page = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    out = await record_post_submit_skills_receipt(
        page,
        required=["reasoning-posture"],
        settle_ms=0,
    )
    assert out["ok"] is False
    assert out["error"]
    assert emitted and emitted[0]["missing"] == ["reasoning-posture"]


@pytest.mark.asyncio
async def test_code_surface_rail_unavailable_treats_receipt_satisfied(
    monkeypatch,
) -> None:
    emitted: list[dict] = []

    async def _rail_missing(_page):
        raise ChatContextSkillsError(
            "Context list is not on the rail — scrape refused until it is expanded"
        )

    def _emit(**kwargs):
        emitted.append(kwargs)
        return None

    monkeypatch.setattr(
        "claude_bundles.skill_context_receipt.scrape_loaded_skills",
        _rail_missing,
    )
    monkeypatch.setattr(
        "claude_bundles.skill_context_receipt.emit_skill_context_loaded",
        _emit,
    )
    page = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    compose = {
        "model_ran": "Model: Opus 5.5 Medium",
        "approval_mode": "auto",
    }
    out = await record_post_submit_skills_receipt(
        page,
        required=["reasoning-posture"],
        claude_surface="code",
        compose_receipt=compose,
        settle_ms=0,
    )
    assert out["ok"] is True
    assert out["rail_unavailable"] is True
    assert out["observed"] == []
    assert out["model_ran"] == "Model: Opus 5.5 Medium"
    assert out["approval_mode"] == "auto"
    assert emitted and emitted[0]["ok"] is True


@pytest.mark.asyncio
async def test_cowork_rail_missing_stays_soft_fail(monkeypatch) -> None:
    async def _rail_missing(_page):
        raise ChatContextSkillsError(
            "Context list is not on the rail — scrape refused until it is expanded"
        )

    monkeypatch.setattr(
        "claude_bundles.skill_context_receipt.scrape_loaded_skills",
        _rail_missing,
    )
    monkeypatch.setattr(
        "claude_bundles.skill_context_receipt.emit_skill_context_loaded",
        lambda **_k: None,
    )
    page = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    out = await record_post_submit_skills_receipt(
        page,
        required=["reasoning-posture"],
        claude_surface="cowork",
        settle_ms=0,
    )
    assert out["ok"] is False
    assert out.get("rail_unavailable") is not True
