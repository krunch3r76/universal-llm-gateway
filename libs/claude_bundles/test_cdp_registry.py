"""Unit tests for CDP port registry — alloc/dealloc/contention without Chrome."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from claude_bundles import cdp_registry as reg

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
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9226))  # tiny pool
    # Avoid real profile paths under ~/.gateway
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


def test_concurrent_register_distinct_ports(isolated_registry: Path) -> None:
    start = threading.Barrier(2)
    hold = threading.Barrier(2)
    results: list[tuple[int, str]] = []
    errors: list[BaseException] = []

    def worker(holder: str) -> None:
        try:
            start.wait(timeout=5)
            r = reg.register_lane(
                holder=holder,
                purpose="ask",
                launch_chrome=_noop_launch,
                is_listening=lambda _p: False,
            )
            results.append((r.port, r.profile_suffix))
            hold.wait(timeout=5)
            reg.deregister_lane(r.registration_id)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("holder-A",)),
        threading.Thread(target=worker, args=("holder-B",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, errors
    ports = {p for p, _ in results}
    suffixes = {s for _, s in results}
    assert len(ports) == 2
    assert len(suffixes) == 2
    assert ports <= {9223, 9224, 9225}


def test_register_exhausted_fails_closed(isolated_registry: Path) -> None:
    regs = []
    for i in range(3):
        regs.append(
            reg.register_lane(
                holder=f"h{i}",
                launch_chrome=_noop_launch,
                is_listening=lambda _p: False,
            )
        )
    with pytest.raises(reg.RegistryExhaustedError, match="hygiene"):
        reg.register_lane(
            holder="overflow",
            launch_chrome=_noop_launch,
            is_listening=lambda _p: False,
        )
    # deregister marks released — still not reusable in v1
    reg.deregister_lane(regs[0].registration_id)
    with pytest.raises(reg.RegistryExhaustedError):
        reg.register_lane(
            holder="after-release",
            launch_chrome=_noop_launch,
            is_listening=lambda _p: False,
        )
    # hygiene reclaims → free again
    result = reg.hygiene_reclaim_released()
    assert regs[0].port in result.reclaimed_ports
    again = reg.register_lane(
        holder="after-hygiene",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert again.port == regs[0].port


def test_deregister_marks_released_not_free(isolated_registry: Path) -> None:
    r = reg.register_lane(
        holder="a",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.deregister_lane(r.registration_id)
    active = reg._load_active()
    assert active[r.registration_id]["status"] == "released"
    assert r.registration_id not in {x.registration_id for x in reg.list_active()}


def test_reattach_same_holder_ok(isolated_registry: Path) -> None:
    r = reg.register_lane(
        holder="seat-1",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg._release_driver_lock(r.registration_id)
    again = reg.reattach(r.registration_id, holder="seat-1")
    assert again.port == r.port
    assert again.registration_id == r.registration_id


def test_reattach_foreign_holder_fails(isolated_registry: Path) -> None:
    r = reg.register_lane(
        holder="seat-1",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg._release_driver_lock(r.registration_id)
    with pytest.raises(reg.RegistryError, match="holder mismatch"):
        reg.reattach(r.registration_id, holder="seat-2")


def test_second_driver_attach_fails_closed(isolated_registry: Path) -> None:
    r = reg.register_lane(
        holder="seat-1",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    # Drop local map but keep the flock fd open (peer process still holds lease).
    held_fd = reg._HELD_LOCKS.pop(r.registration_id)
    errors: list[BaseException] = []

    def peer() -> None:
        try:
            reg._claim_driver_lock(r.registration_id)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=peer)
    thread.start()
    thread.join(timeout=5)
    assert len(errors) == 1
    assert isinstance(errors[0], reg.RegistryBusyError)
    reg._HELD_LOCKS[r.registration_id] = held_fd


def test_select_free_skips_excluded_and_listening() -> None:
    port = reg.select_free_registry_port(
        lambda p: p == 9223,
        exclude={9224},
        port_range=range(9223, 9226),
    )
    assert port == 9225


def test_launch_failure_rolls_back_allocating(isolated_registry: Path) -> None:
    def boom(port: int, profile: Path) -> int:
        profile.mkdir(parents=True, exist_ok=True)
        raise RuntimeError("chrome fail")

    with pytest.raises(RuntimeError, match="chrome fail"):
        reg.register_lane(
            holder="a",
            launch_chrome=boom,
            is_listening=lambda _p: False,
        )
    assert reg.list_active() == []
    assert reg._load_active() == {}
    # Port must be reusable after rollback (no released tombstone).
    again = reg.register_lane(
        holder="b",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert again.port == 9223


def test_reattach_emits_reattached(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        reg._events,
        "emit",
        lambda event: events.append(event.signal),
    )
    r = reg.register_lane(
        holder="seat-1",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    events.clear()
    reg._release_driver_lock(r.registration_id)
    reg.reattach(r.registration_id, holder="seat-1")
    assert events == ["cdp.port.reattached"]


def test_used_ports_snapshot_includes_active(isolated_registry: Path) -> None:
    r = reg.register_lane(
        holder="a", launch_chrome=_noop_launch, is_listening=lambda _p: False
    )
    assert r.port in reg.used_ports_snapshot()


def test_process_holds_driver_lock_after_register(isolated_registry: Path) -> None:
    r = reg.register_lane(
        holder="a", launch_chrome=_noop_launch, is_listening=lambda _p: False
    )
    assert reg.process_holds_driver_lock(r.registration_id)
    reg._release_driver_lock(r.registration_id)
    assert not reg.process_holds_driver_lock(r.registration_id)


def test_is_driver_lock_held_reflects_peer(isolated_registry: Path) -> None:
    r = reg.register_lane(
        holder="a", launch_chrome=_noop_launch, is_listening=lambda _p: False
    )
    assert reg.is_driver_lock_held(r.registration_id)
    reg._release_driver_lock(r.registration_id)
    assert not reg.is_driver_lock_held(r.registration_id)


def test_active_registration_dicts_enriched_fields(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = reg.register_lane(
        holder="seat-a",
        purpose="ask",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    rows = reg.active_registration_dicts()
    assert len(rows) == 1
    row = rows[0]
    assert row["registration_id"] == r.registration_id
    assert row["driver_pid"] == reg._load_active()[r.registration_id]["holder_pid"]
    assert row["attached"] is True
    assert row["chrome_pid"] == 1


def test_hygiene_rmtree_released_profile(isolated_registry: Path) -> None:
    r = reg.register_lane(
        holder="a",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    profile = r.profile
    assert profile.exists()
    reg.deregister_lane(r.registration_id)

    result = reg.hygiene_reclaim_released()
    assert r.port in result.reclaimed_ports
    assert str(profile) in result.removed_profiles
    assert not profile.exists()


def test_hygiene_skips_primary_profile(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    primary = tmp_path / "claude-ai-chrome-profile"
    primary.mkdir()
    (primary / "OptGuideOnDeviceModel").mkdir()
    monkeypatch.setattr(reg.cdp_lane, "PRIMARY_PROFILE", primary)
    monkeypatch.setattr(reg.cdp_lane, "chrome_port_for_profile", lambda _p: None)

    with reg._store.ports_lock():
        active = reg._store.load_active()
        active["dead"] = {
            "registration_id": "dead",
            "port": 9223,
            "profile_suffix": "primary",
            "profile": str(primary),
            "holder": "a",
            "status": "released",
        }
        reg._store.write_active(active)

    result = reg.hygiene_reclaim_released()
    assert primary.exists()
    assert str(primary) not in result.removed_profiles


def test_hygiene_skips_live_profile(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = reg.cdp_lane.profile_for("x").parent
    profile = profiles / "claude-ai-chrome-profile-reg-livebeef"
    profile.mkdir(parents=True)
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    monkeypatch.setattr(reg.cdp_lane, "chrome_port_for_profile", lambda p: 9225)

    with reg._store.ports_lock():
        active = reg._store.load_active()
        active["live"] = {
            "registration_id": "live",
            "port": 9224,
            "profile_suffix": "reg-livebeef",
            "profile": str(profile),
            "holder": "a",
            "status": "released",
        }
        reg._store.write_active(active)

    result = reg.hygiene_reclaim_released()
    assert profile.exists()
    assert str(profile) not in result.removed_profiles
