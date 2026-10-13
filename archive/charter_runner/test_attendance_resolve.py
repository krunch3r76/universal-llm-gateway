"""Async attendance resolution — bus client path and fail-loud defaults."""

from __future__ import annotations

import logging

import pytest

from scripts.model_manager.ui.controller.charter_runner.attendance import (
    admission_mode_for_attendance,
    resolve_attendance,
)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_resolve_attendance_bus_unreachable_logs_warning_and_defaults_attended(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _fail_fetch_turns(_root_id: str) -> list[dict]:
        raise ConnectionError("bus unreachable")

    async def _fail_fetch_thread(_root_id: str) -> dict:
        raise ConnectionError("bus unreachable")

    import scripts.model_manager.ui.controller.charter_runner.bus_client as bc

    monkeypatch.setattr(bc, "fetch_turns", _fail_fetch_turns)
    monkeypatch.setattr(bc, "fetch_thread", _fail_fetch_thread)
    caplog.set_level(logging.WARNING)

    result = await resolve_attendance("6091")

    assert result == "attended"
    assert any(
        "6091" in rec.message and rec.levelno >= logging.WARNING
        for rec in caplog.records
    )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_resolve_attendance_autonomous_tag_maps_to_admission_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _empty_turns(_root_id: str) -> list[dict]:
        return []

    async def _autonomous_thread(_root_id: str) -> dict:
        return {"tags": ["attendance:autonomous", "charter-runner"]}

    import scripts.model_manager.ui.controller.charter_runner.bus_client as bc

    monkeypatch.setattr(bc, "fetch_turns", _empty_turns)
    monkeypatch.setattr(bc, "fetch_thread", _autonomous_thread)

    attendance = await resolve_attendance("6091")

    assert attendance == "autonomous"
    assert admission_mode_for_attendance(attendance) == "autonomous"
