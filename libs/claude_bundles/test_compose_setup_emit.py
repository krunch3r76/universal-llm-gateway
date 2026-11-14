"""Hermetic tests for compose_setup_emit wiring (a:43854 compose v2 round 2)."""

from __future__ import annotations

import pytest

from claude_bundles import compose_setup_emit as cse


@pytest.mark.asyncio
async def test_ensure_bare_new_compose_passes_model_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def _fake_ensure(page, **kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "step": "compose_v2",
            "quota_weekly_pct": 42,
            "model_chip": {"ok": True},
        }

    monkeypatch.setattr(
        "claude_bundles.events_compose_attest.emit_compose_attested_from_result",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.ensure_cowork_auto",
        _fake_ensure,
    )
    page = object()
    await cse.ensure_bare_new_compose(
        page,
        model_request="opus-5.5-high",
        stargate_execution_id="sg-id",
        satellite_execution_id="sat-id",
    )
    assert captured["model_request"] == "opus-5.5"
    assert captured["model_effort"] == "high"
    snap = cse.last_bare_compose_ensure()
    assert snap is not None
    assert snap["quota_weekly_pct"] == 42


@pytest.mark.offline
def test_compose_runner_quota_fields_from_last_ensure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cse,
        "_LAST_BARE_COMPOSE_ENSURE",
        {"quota_weekly_pct": 91, "step": "compose_v2"},
    )
    assert cse.compose_runner_quota_fields() == {
        "quota_weekly_pct": 91,
        "quota_near": False,
    }
    monkeypatch.setattr(
        cse,
        "_LAST_BARE_COMPOSE_ENSURE",
        {"quota_weekly_pct": 96, "step": "quota_near", "failure_class": "quota_near"},
    )
    assert cse.compose_runner_quota_fields() == {
        "quota_weekly_pct": 96,
        "quota_near": True,
    }


@pytest.mark.offline
def test_compose_runner_quota_fields_absent_banner_explicit_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cse, "_LAST_BARE_COMPOSE_ENSURE", None)
    assert cse.compose_runner_quota_fields() == {
        "quota_weekly_pct": None,
        "quota_near": False,
    }
    monkeypatch.setattr(
        cse,
        "_LAST_BARE_COMPOSE_ENSURE",
        {"ok": True, "step": "compose_v2", "quota_weekly_pct": None},
    )
    assert cse.compose_runner_quota_fields() == {
        "quota_weekly_pct": None,
        "quota_near": False,
    }
