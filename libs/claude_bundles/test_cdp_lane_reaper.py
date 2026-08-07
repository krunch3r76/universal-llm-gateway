"""Hermetic tests for orphaned_alive CDP lane reaper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from claude_bundles import cdp_lane_reaper as reaper
from claude_bundles import cdp_registry as reg

pytestmark = pytest.mark.offline

_TTL = reaper.orphaned_alive_ttl_s()
_NOW = 1_700_000_000.0


def _orphan_row(
    *,
    rid: str = "orph-1",
    port: int = 9223,
    orphaned_at: float | None = _NOW - _TTL - 60,
    chrome_pid: int | None = 999,
    profile_suffix: str = "reg-dead01",
) -> dict[str, Any]:
    return {
        "registration_id": rid,
        "port": port,
        "profile_suffix": profile_suffix,
        "holder": "seat-a",
        "status": "orphaned_alive",
        "orphaned_at": orphaned_at,
        "orphan_reason": "probe_failed",
        "chrome_pid": chrome_pid,
    }


def test_trigger_a_port_not_listening_reaped() -> None:
    active = {"orph-1": _orphan_row(port=9223)}
    reaped = reaper.reap_orphaned_alive_rows(
        active,
        listen=lambda _p: False,
        now=_NOW,
        pid_alive=lambda _pid: True,
        is_attached=lambda _rid: False,
    )
    assert reaped == ["orph-1"]
    row = active["orph-1"]
    assert row["status"] == "released"
    assert row["reaped_orphaned_alive"] == "dead"
    assert isinstance(row["released_at"], float)


def test_trigger_a_chrome_pid_dead_reaped() -> None:
    active = {"orph-1": _orphan_row(chrome_pid=42, orphaned_at=None)}
    reaped = reaper.reap_orphaned_alive_rows(
        active,
        listen=lambda _p: True,
        now=_NOW,
        pid_alive=lambda pid: pid != 42,
        is_attached=lambda _rid: False,
    )
    assert reaped == ["orph-1"]
    assert active["orph-1"]["status"] == "released"
    assert active["orph-1"]["reaped_orphaned_alive"] == "dead"


def test_trigger_b_ttl_unattached_kills_before_release() -> None:
    active = {"orph-1": _orphan_row(orphaned_at=_NOW - _TTL - 1)}
    killed: list[int] = []

    def kill(port: int) -> None:
        killed.append(port)
        assert active["orph-1"]["status"] == "orphaned_alive"

    reaper.reap_orphaned_alive_rows(
        active,
        listen=lambda _p: True,
        now=_NOW,
        pid_alive=lambda _pid: True,
        kill_listener=kill,
        is_attached=lambda _rid: False,
        include_ttl_reap=True,
    )
    assert killed == [9223]
    assert active["orph-1"]["status"] == "released"
    assert active["orph-1"]["reaped_orphaned_alive"] == "ttl"


def test_ttl_skips_operator_proxy_orphaned_alive() -> None:
    """OP false-death rows must not have chrome killed by orphaned_alive TTL."""
    row = _orphan_row(orphaned_at=_NOW - _TTL - 1)
    row["purpose"] = "operator-proxy"
    active = {"orph-op": row}
    killed: list[int] = []
    reaper.reap_orphaned_alive_rows(
        active,
        listen=lambda _p: True,
        now=_NOW,
        pid_alive=lambda _pid: True,
        kill_listener=lambda p: killed.append(p),
        is_attached=lambda _rid: False,
        include_ttl_reap=True,
    )
    assert killed == []
    assert active["orph-op"]["status"] == "orphaned_alive"


def test_negative_alive_within_ttl_untouched() -> None:
    active = {"orph-1": _orphan_row(orphaned_at=_NOW - 60)}
    reaped = reaper.reap_orphaned_alive_rows(
        active,
        listen=lambda _p: True,
        now=_NOW,
        pid_alive=lambda _pid: True,
        is_attached=lambda _rid: False,
        include_ttl_reap=True,
    )
    assert reaped == []
    assert active["orph-1"]["status"] == "orphaned_alive"


def test_negative_driver_lock_held_untouched_past_ttl() -> None:
    active = {"orph-1": _orphan_row(orphaned_at=_NOW - _TTL - 1)}
    killed: list[int] = []
    reaped = reaper.reap_orphaned_alive_rows(
        active,
        listen=lambda _p: True,
        now=_NOW,
        pid_alive=lambda _pid: True,
        kill_listener=lambda port: killed.append(port),
        is_attached=lambda _rid: True,
        include_ttl_reap=True,
    )
    assert reaped == []
    assert killed == []
    assert active["orph-1"]["status"] == "orphaned_alive"


def test_negative_missing_orphaned_at_listening_untouched() -> None:
    active = {"orph-1": _orphan_row(orphaned_at=None, chrome_pid=999)}
    reaped = reaper.reap_orphaned_alive_rows(
        active,
        listen=lambda _p: True,
        now=_NOW,
        pid_alive=lambda _pid: True,
        is_attached=lambda _rid: False,
        include_ttl_reap=True,
    )
    assert reaped == []
    assert active["orph-1"]["status"] == "orphaned_alive"


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    trash = tmp_path / "reclaim-trash"
    monkeypatch.setattr(reg, "RECLAIM_TRASH_DIR", trash)
    monkeypatch.setattr(reg.cdp_lane, "chrome_port_for_profile", lambda _p: None)
    return root


def test_integration_trigger_a_hygiene_reclaims_same_sweep(
    isolated_registry: Path,
) -> None:
    profile = reg.cdp_lane.profile_for("reg-int01")
    profile.mkdir(parents=True)
    row = {
        "registration_id": "int-1",
        "port": 9223,
        "profile_suffix": "reg-int01",
        "profile": str(profile),
        "holder": "seat-a",
        "status": "orphaned_alive",
        "orphaned_at": _NOW,
        "orphan_reason": "cse_not_found",
    }
    with reg._store.ports_lock():
        reg._store.write_active({"int-1": row})

    result = reg.hygiene_reclaim_extended(
        is_listening=lambda _p: False,
        empty_trash=False,
    )
    assert 9223 in result.reclaimed_ports
    assert str(profile) in result.removed_profiles
    assert "int-1" not in reg._load_active()
