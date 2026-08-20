"""Driving-operator registry row: birth, reuse, hop exclusion, no dormant promotion."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_registry.driving_seat import ensure_driving_operator_seat
from claude_bundles.cdp_registry.models import RegistryError

pytestmark = pytest.mark.offline


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
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9226))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    return root


def _noop_launch(port: int, profile: Path) -> int:
    profile.mkdir(parents=True, exist_ok=True)
    return 1


def _ensure(**kwargs: object) -> object:
    defaults: dict[str, object] = {
        "holder": "operator-seat",
        "parent_thread": "9497",
        "purpose": "operator-proxy",
        "mission_kind": "root",
        "launch_chrome": _noop_launch,
        "is_listening": lambda _p: False,
    }
    defaults.update(kwargs)
    return ensure_driving_operator_seat(**defaults)  # type: ignore[arg-type]


def test_ensure_mints_one_listable_root_row(isolated_registry: Path) -> None:
    first = _ensure()
    assert first.purpose == "operator-proxy"
    assert first.mission_kind == "root"
    assert first.parent_thread == "9497"
    row = reg._store.load_active()[first.registration_id]
    assert row["status"] == "active"


def test_ensure_reuses_existing_listable_root(isolated_registry: Path) -> None:
    first = _ensure()
    second = _ensure()
    assert second.registration_id == first.registration_id
    assert len(reg.list_active()) == 1


def test_ensure_does_not_reuse_listable_hop(isolated_registry: Path) -> None:
    hop = reg.register_lane(
        holder="hop-chrome",
        purpose="operator-proxy",
        mission_kind="hop",
        parent_thread="9497",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    driving = _ensure()
    assert driving.registration_id != hop.registration_id
    assert driving.mission_kind == "root"
    assert hop.registration_id in reg._store.load_active()
    assert driving.registration_id in reg._store.load_active()


def test_ensure_relaunches_dormant_unbound_row(isolated_registry: Path) -> None:
    first = _ensure()
    active = reg._store.load_active()
    active[first.registration_id]["status"] = "dormant"
    active[first.registration_id]["seat_lane"] = None
    active[first.registration_id]["seat_closed_at"] = None
    active[first.registration_id]["seat_bound_at"] = None
    reg._store.write_active(active)
    reg._release_driver_lock(first.registration_id)
    again = _ensure()
    assert again.registration_id == first.registration_id
    assert len(reg._store.load_active()) == 1
    assert reg._store.load_active()[again.registration_id]["status"] == "active"


def test_ensure_raises_when_two_listable_driving_rows_exist(
    isolated_registry: Path,
) -> None:
    _ensure()
    reg.register_lane(
        holder="second-root",
        purpose="operator-proxy",
        mission_kind="root",
        parent_thread="9497",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    with pytest.raises(RegistryError, match="ambiguous listable driving"):
        _ensure()


def test_ensure_rejects_hop_as_driving_kind(isolated_registry: Path) -> None:
    with pytest.raises(RegistryError, match="cannot be mission_kind=hop"):
        _ensure(mission_kind="hop")
