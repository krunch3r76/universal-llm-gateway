"""Tests for manage API whoami identity op."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from scripts.model_manager.ui import api_dispatch


@pytest.mark.offline
@pytest.mark.asyncio
async def test_whoami_returns_pid_code_version_and_start_time(monkeypatch) -> None:
    monkeypatch.setattr(api_dispatch, "resolve_code_version", lambda: "a" * 40)
    monkeypatch.setattr(api_dispatch, "process_age_s", lambda: 120.0)
    fixed_now = api_dispatch.datetime(2026, 7, 31, 21, 0, 0, tzinfo=api_dispatch.UTC)
    monkeypatch.setattr(api_dispatch, "datetime", MagicMock(now=lambda tz=None: fixed_now))

    ctl = MagicMock()
    result = await api_dispatch.execute(ctl, "whoami", "", {})

    assert result["pid"] == os.getpid()
    assert result["code_version"] == "a" * 40
    assert result["process_start_time"] == "2026-07-31T20:58:00+00:00"
