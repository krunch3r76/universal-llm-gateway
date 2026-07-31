from __future__ import annotations

from typing import Any

import pytest

from services.rag.config import RagConfig, ScopeDefinition, WatchDirectory
from services.rag.rag_service import lifecycle, scope_freshness, state


class _FakePropertyIndex:
    def __init__(self) -> None:
        self._watermark_checks = [
            ["vocabulary"],
            ["vocabulary"],
            ["vocabulary"],
            [],
        ]

    def check_watermarks(self, steps: list[str], *, reference: str) -> list[str]:
        del steps, reference
        return self._watermark_checks.pop(0)


@pytest.mark.asyncio
async def test_startup_scope_repair_retries_until_watermark_catches_up(
    monkeypatch: Any,
) -> None:
    config = RagConfig(
        watch_directories=[WatchDirectory(path="/tmp")],
        scopes={"docs": ScopeDefinition(prefixes=["/tmp"], description="Docs")},
    )
    fake_index = _FakePropertyIndex()
    attempts: list[list[str]] = []
    sleeps: list[float] = []

    async def fake_repair(**kwargs: Any) -> None:
        attempts.append(list(kwargs["stale_scopes"]))

    async def fake_sleep(delay_s: float) -> None:
        sleeps.append(delay_s)

    monkeypatch.setattr(state, "_property_index", fake_index)
    monkeypatch.setattr(scope_freshness, "detect_stale_scopes", lambda **kwargs: [])
    monkeypatch.setattr(scope_freshness, "run_scope_freshness_repair", fake_repair)
    monkeypatch.setattr(scope_freshness.asyncio, "sleep", fake_sleep)

    await lifecycle._run_startup_scope_freshness_repair(config)

    assert attempts == [["docs"], ["docs"]]
    assert sleeps == [15.0]
