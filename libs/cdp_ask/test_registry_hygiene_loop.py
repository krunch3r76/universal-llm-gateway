"""Tests for ULG-owned CDP registry hygiene loop."""

from __future__ import annotations

import asyncio

import pytest

from cdp_ask.registry_hygiene_loop import (
    RegistryHygieneLoop,
    hygiene_interval_s,
    run_hygiene_once,
)


def test_hygiene_interval_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDP_REGISTRY_HYGIENE_INTERVAL_S", raising=False)
    assert hygiene_interval_s() == 1200.0
    monkeypatch.setenv("CDP_REGISTRY_HYGIENE_INTERVAL_S", "90")
    assert hygiene_interval_s() == 90.0
    monkeypatch.setenv("CDP_REGISTRY_HYGIENE_INTERVAL_S", "nope")
    assert hygiene_interval_s() == 1200.0


def test_run_hygiene_once_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        reclaimed_ports = [9223]
        removed_profiles = ["/tmp/fake-profile"]

    monkeypatch.setattr(
        "claude_bundles.cdp_registry.hygiene_reclaim_extended",
        lambda **_: _Result(),
    )
    summary = run_hygiene_once()
    assert summary["reclaimed_ports"] == [9223]
    assert summary["removed_profiles"] == ["/tmp/fake-profile"]


@pytest.mark.asyncio
async def test_loop_survives_sweep_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _boom() -> dict[str, list[str]]:
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "cdp_ask.registry_hygiene_loop.run_hygiene_once",
        _boom,
    )
    loop = RegistryHygieneLoop(interval_s=0.05)
    await loop.start()
    assert loop.running
    await asyncio.sleep(0.18)
    await loop.stop()
    assert not loop.running
    assert calls["n"] >= 2
