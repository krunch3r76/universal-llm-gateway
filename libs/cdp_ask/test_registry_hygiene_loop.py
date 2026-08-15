"""Tests for ULG-owned CDP registry hygiene loop."""

from __future__ import annotations

import asyncio

import pytest

from cdp_ask.registry_hygiene_loop import (
    RegistryHygieneLoop,
    hygiene_interval_s,
    run_hygiene_once,
    run_orphan_scan_once,
)


def test_hygiene_interval_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDP_REGISTRY_HYGIENE_INTERVAL_S", raising=False)
    assert hygiene_interval_s() == 1200.0
    monkeypatch.setenv("CDP_REGISTRY_HYGIENE_INTERVAL_S", "90")
    assert hygiene_interval_s() == 90.0
    monkeypatch.setenv("CDP_REGISTRY_HYGIENE_INTERVAL_S", "nope")
    assert hygiene_interval_s() == 1200.0


class _ReclaimResult:
    reclaimed_ports = [9223]
    removed_profiles = ["/tmp/fake-profile"]


def _stub_sweep(monkeypatch: pytest.MonkeyPatch, order: list[str]) -> None:
    """Neutralize every registry-touching leg of one hygiene pass but the order."""
    from claude_bundles.cdp_registry import dormant_drain

    def _drain(**kwargs: object) -> dormant_drain.DrainResult:
        order.append("drain")
        _drain.kwargs = kwargs  # type: ignore[attr-defined]
        return dormant_drain.DrainResult(dormant=["reg-a"], released=[], protected={})

    def _reclaim_dormant(**_: object) -> list[str]:
        order.append("reclaim_dormant")
        return ["reg-old"]

    def _reclaim_extended(**_: object) -> _ReclaimResult:
        order.append("reclaim_extended")
        return _ReclaimResult()

    monkeypatch.setattr(dormant_drain, "drain_live_hosts_to_dormant", _drain)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.reclaim_dormant_rows", _reclaim_dormant
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.hygiene_reclaim_extended", _reclaim_extended
    )
    monkeypatch.setattr(
        "claude_bundles.cse_session_obligations.sweep_wake_owed_ttl",
        lambda **_: [],
    )
    _stub_sweep.drain = _drain  # type: ignore[attr-defined]


def test_run_hygiene_once_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_sweep(monkeypatch, [])
    summary = run_hygiene_once()
    assert summary["reclaimed_ports"] == [9223]
    assert summary["removed_profiles"] == ["/tmp/fake-profile"]


def test_hygiene_drains_to_dormant_before_reclaiming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parking frees ports in the same sweep that reclaims aged rows and profiles."""
    order: list[str] = []
    _stub_sweep(monkeypatch, order)

    summary = run_hygiene_once()

    assert order == ["drain", "reclaim_dormant", "reclaim_extended"]
    assert summary["drained"]["dormant"] == ["reg-a"]
    assert summary["dormant_reclaimed"] == ["reg-old"]


def test_hygiene_protects_hosts_with_an_in_flight_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The drain must consult the live lane lock, not just registry status."""
    from cdp_ask import followup

    _stub_sweep(monkeypatch, [])
    run_hygiene_once()

    assert _stub_sweep.drain.kwargs["is_busy"] is followup.lane_in_flight  # type: ignore[attr-defined]


def test_run_orphan_scan_once_emits_event_without_registry_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-2: scheduled orphan scan emits observation event and mutates nothing."""
    from claude_bundles import cdp_orphans, cdp_registry

    mutations: list[str] = []

    def _track(name: str):
        def _inner(*_args: object, **_kwargs: object) -> None:
            mutations.append(name)

        return _inner

    monkeypatch.setattr(
        cdp_registry._store,
        "write_active",
        _track("write_active"),
    )
    monkeypatch.setattr(
        cdp_registry._store,
        "append_log",
        _track("append_log"),
    )
    monkeypatch.setattr(
        cdp_registry,
        "deregister_lane",
        _track("deregister_lane"),
    )
    monkeypatch.setattr(
        cdp_registry,
        "hygiene_reclaim_extended",
        _track("hygiene_reclaim_extended"),
    )

    captured: list[object] = []
    monkeypatch.setattr(
        cdp_registry._events,
        "emit",
        lambda event: captured.append(event),
    )
    monkeypatch.setattr(cdp_orphans, "probe_live_ports", lambda port_range=None: [])
    monkeypatch.setattr(cdp_orphans, "_registered_ports", lambda: set())

    summary = run_orphan_scan_once()

    assert len(captured) == 1
    event = captured[0]
    assert event.signal == "cdp.port.orphan_scan"
    assert event.payload == {
        "ports_live": 0,
        "ports_skipped_registered": 0,
        "ports_examined": 0,
        "matched_count": 0,
        "rejected_count": 0,
        "unevaluable_count": 0,
        "closable_count": 0,
        "protected_count": 0,
        "reclaim_enabled": False,
    }
    assert summary["ports_live"] == 0
    assert mutations == []


@pytest.mark.asyncio
async def test_loop_runs_orphan_scan_on_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orphan_calls = {"n": 0}

    def _scan() -> dict[str, int]:
        orphan_calls["n"] += 1
        return {
            "ports_live": 0,
            "ports_skipped_registered": 0,
            "ports_examined": 0,
            "matched_count": 0,
            "rejected_count": 0,
            "unevaluable_count": 0,
        }

    monkeypatch.setattr(
        "cdp_ask.registry_hygiene_loop.run_hygiene_once",
        lambda: {"reclaimed_ports": [], "removed_profiles": []},
    )
    monkeypatch.setattr(
        "cdp_ask.registry_hygiene_loop.run_orphan_scan_once",
        _scan,
    )
    loop = RegistryHygieneLoop(interval_s=0.05)
    await loop.start()
    await asyncio.sleep(0.18)
    await loop.stop()
    assert orphan_calls["n"] >= 2


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
