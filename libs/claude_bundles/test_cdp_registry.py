"""Unit tests for CDP port registry — alloc/dealloc/contention without Chrome."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from claude_bundles import cdp_orphans
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


def test_register_lane_records_display(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CDP_DISPLAY", ":7")
    r = reg.register_lane(
        holder="display-test",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert r.display == ":7"
    active = reg._load_active()
    assert active[r.registration_id]["display"] == ":7"


def test_register_lane_records_mission_lineage(isolated_registry: Path) -> None:
    r = reg.register_lane(
        holder="hop-test",
        purpose="operator-proxy",
        mission_kind="hop",
        parent_thread="6655",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert r.mission_kind == "hop"
    assert r.parent_thread == "6655"
    row = reg._load_active()[r.registration_id]
    assert row["mission_kind"] == "hop"
    assert row["parent_thread"] == "6655"


def test_register_lane_rejects_unknown_mission_kind(isolated_registry: Path) -> None:
    with pytest.raises(reg.RegistryError, match="mission_kind"):
        reg.register_lane(
            holder="bad-kind",
            mission_kind="nested",
            launch_chrome=_noop_launch,
            is_listening=lambda _p: False,
        )


def test_deregister_kill_true_on_orphaned_alive(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", lambda port: killed.append(port))
    r = reg.register_lane(
        holder="a",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.deregister_lane(
        r.registration_id,
        reason="probe_failed",
        is_listening=lambda _p: True,
    )
    assert reg._load_active()[r.registration_id]["status"] == "orphaned_alive"
    assert killed == []
    reg.deregister_lane(
        r.registration_id,
        kill=True,
        is_listening=lambda _p: True,
    )
    assert killed == [r.port]
    assert reg._load_active()[r.registration_id]["status"] == "released"


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
    # deregister marks released — reclaim-on-register recycles the port
    reg.deregister_lane(regs[0].registration_id)
    again = reg.register_lane(
        holder="after-release",
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
    reg.deregister_lane(r.registration_id, is_listening=lambda _p: False)
    active = reg._load_active()
    assert active[r.registration_id]["status"] == "released"
    assert r.registration_id not in {
        x.registration_id
        for x in reg.list_active()
        if reg._load_active()[x.registration_id]["status"] == "active"
    }


def test_deregister_voluntary_default_kills_listener(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", lambda port: killed.append(port))
    r = reg.register_lane(
        holder="a",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.deregister_lane(r.registration_id, is_listening=lambda _p: True)
    assert killed == [r.port]
    assert reg._load_active()[r.registration_id]["status"] == "released"


def test_deregister_cse_not_found_never_kills(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", lambda port: killed.append(port))
    r = reg.register_lane(
        holder="a",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.deregister_lane(
        r.registration_id,
        reason="cse_not_found",
        is_listening=lambda _p: True,
    )
    assert killed == []
    row = reg._load_active()[r.registration_id]
    assert row["status"] == "orphaned_alive"
    assert row["orphan_reason"] == "cse_not_found"
    visible = cdp_orphans.registered_lane_dicts()
    assert any(item["registration_id"] == r.registration_id for item in visible)


def test_list_capacity_excludes_orphaned_alive(isolated_registry: Path) -> None:
    r = reg.register_lane(
        holder="a",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.deregister_lane(
        r.registration_id,
        reason="cse_not_found",
        is_listening=lambda _p: True,
    )
    assert [x.registration_id for x in reg.list_active()] == [r.registration_id]
    assert reg.list_capacity() == []
    assert reg.count_capacity_lanes() == 0


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


def test_registered_lane_dicts_enriched_fields(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = reg.register_lane(
        holder="seat-a",
        purpose="ask",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    rows = cdp_orphans.registered_lane_dicts()
    assert len(rows) == 1
    row = rows[0]
    assert row["registration_id"] == r.registration_id
    assert row["driver_pid"] == reg._load_active()[r.registration_id]["holder_pid"]
    assert row["attached"] is True
    assert row["chrome_pid"] == 1


def test_hygiene_rmtree_released_profile(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    trash = tmp_path / "reclaim-trash"
    monkeypatch.setattr(reg, "RECLAIM_TRASH_DIR", trash)
    r = reg.register_lane(
        holder="a",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    profile = r.profile
    assert profile.exists()
    reg.deregister_lane(r.registration_id)

    result = reg.hygiene_reclaim_extended(empty_trash=False)
    assert r.port in result.reclaimed_ports
    assert str(profile) in result.removed_profiles
    assert not profile.exists()
    assert any(trash.iterdir())


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
    assert 9223 not in result.reclaimed_ports
    active = reg._load_active()
    assert "dead" in active
    assert active["dead"]["status"] == "orphaned_retry"


def test_orphan_sweep_never_primary(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    trash = tmp_path / "reclaim-trash"
    monkeypatch.setattr(reg, "RECLAIM_TRASH_DIR", trash)
    primary = tmp_path / "profiles" / "claude-ai-chrome-profile"
    primary.mkdir(parents=True)
    orphan = primary.parent / "claude-ai-chrome-profile-reg-orphan01"
    orphan.mkdir()
    kept_primary = primary.parent / "claude-ai-chrome-profile"
    monkeypatch.setattr(reg.cdp_lane, "PRIMARY_PROFILE", kept_primary)
    monkeypatch.setattr(reg.cdp_lane, "chrome_port_for_profile", lambda _p: None)

    result = reg.hygiene_reclaim_extended(include_stale_active=False)
    assert kept_primary.exists()
    assert not orphan.exists()
    assert str(orphan) in result.removed_profiles


def test_hygiene_skips_live_profile_keeps_orphaned_retry(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    trash = tmp_path / "reclaim-trash"
    monkeypatch.setattr(reg, "RECLAIM_TRASH_DIR", trash)
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
    assert 9224 not in result.reclaimed_ports
    active = reg._load_active()
    assert "live" in active
    assert active["live"]["status"] == "orphaned_retry"


def test_list_cli_emits_object_not_bare_array(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.find_orphans",
        lambda: cdp_orphans.OrphanScanResult(
            matched=(),
            rejected=(),
            unevaluable=(),
            ports_live=0,
            ports_skipped_registered=0,
        ),
    )
    cli = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "cortex"
        / "cdp_registry_cli.py"
    )
    proc = subprocess.run(
        [sys.executable, str(cli), "list"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    assert isinstance(data, dict)
    assert set(data) == {"lanes", "orphans", "orphan_scan", "liveness_authority"}
    assert data["liveness_authority"] == "attachment_only"
    assert isinstance(data["lanes"], list)
    assert isinstance(data["orphans"], list)


def test_list_surface_orphan_scan_distinguishes_empty_triples_on_stdout(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-1: list-lanes orphan_scan stdout distinguishes zero-live vs all-registered."""
    reg_profile = tmp_path / "claude-ai-chrome-profile-reg-live01"

    monkeypatch.setattr(cdp_orphans, "probe_live_ports", lambda port_range=None: [])
    monkeypatch.setattr(cdp_orphans, "_registered_ports", lambda: set())
    no_live = cdp_orphans.list_surface_payload()

    monkeypatch.setattr(
        cdp_orphans,
        "probe_live_ports",
        lambda port_range=None: [
            cdp_orphans.LivePort(
                port=9229,
                profile=reg_profile,
                page_urls=(),
                has_live_cse=False,
            )
        ],
    )
    monkeypatch.setattr(cdp_orphans, "_registered_ports", lambda: {9229})
    all_registered = cdp_orphans.list_surface_payload()

    assert no_live["orphan_scan"] != all_registered["orphan_scan"]
    assert no_live["orphan_scan"]["ports_live"] == 0
    assert no_live["orphan_scan"]["ports_skipped_registered"] == 0
    assert all_registered["orphan_scan"]["ports_live"] == 1
    assert all_registered["orphan_scan"]["ports_skipped_registered"] == 1
    assert no_live["orphans"] == []
    assert all_registered["orphans"] == []


def test_orphan_scan_emits_event_every_scan(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reg_profile = tmp_path / "claude-ai-chrome-profile-reg-deadbeef"
    captured: list[Any] = []
    monkeypatch.setattr(
        reg._events,
        "emit",
        lambda event: captured.append(event),
    )
    monkeypatch.setattr(
        cdp_orphans,
        "probe_live_ports",
        lambda port_range=None: [
            cdp_orphans.LivePort(
                port=9229,
                profile=reg_profile,
                page_urls=(),
                has_live_cse=False,
            )
        ],
    )
    monkeypatch.setattr(cdp_orphans, "_registered_ports", lambda: set())
    monkeypatch.setattr(cdp_orphans, "_pid_listening_on", lambda _p: 4242)
    monkeypatch.setattr(cdp_orphans, "_process_uptime_s", lambda _p: 1.0)

    cdp_orphans.find_orphans()
    assert len(captured) == 1
    event = captured[0]
    assert event.signal == "cdp.port.orphan_scan"
    assert event.role == "observation"
    assert event.payload == {
        "ports_live": 1,
        "ports_skipped_registered": 0,
        "ports_examined": 1,
        "matched_count": 1,
        "rejected_count": 0,
        "unevaluable_count": 0,
        "closable_count": 0,
        "protected_count": 0,
        "reclaim_enabled": False,
    }
    assert not (isolated_registry / "registry.jsonl").exists()


def test_orphan_scan_events_distinguish_no_live_from_all_registered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reg_profile = tmp_path / "claude-ai-chrome-profile-reg-live01"
    captured: list[Any] = []

    def _capture(event: Any) -> None:
        captured.append(event)

    monkeypatch.setattr(reg._events, "emit", _capture)

    monkeypatch.setattr(cdp_orphans, "probe_live_ports", lambda port_range=None: [])
    monkeypatch.setattr(cdp_orphans, "_registered_ports", lambda: set())
    cdp_orphans.find_orphans()

    monkeypatch.setattr(
        cdp_orphans,
        "probe_live_ports",
        lambda port_range=None: [
            cdp_orphans.LivePort(
                port=9229,
                profile=reg_profile,
                page_urls=(),
                has_live_cse=False,
            )
        ],
    )
    monkeypatch.setattr(cdp_orphans, "_registered_ports", lambda: {9229})
    cdp_orphans.find_orphans()

    assert len(captured) == 2
    no_live_event, all_registered_event = captured
    assert no_live_event.signal == "cdp.port.orphan_scan"
    assert all_registered_event.signal == "cdp.port.orphan_scan"
    assert no_live_event.payload != all_registered_event.payload
    assert no_live_event.payload["ports_live"] == 0
    assert all_registered_event.payload["ports_live"] == 1
    assert all_registered_event.payload["ports_skipped_registered"] == 1


def test_hygiene_reclaim_success_log_carries_reclaimed_not_stale_status(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    trash = tmp_path / "reclaim-trash"
    monkeypatch.setattr(reg, "RECLAIM_TRASH_DIR", trash)
    profiles = reg.cdp_lane.profile_for("x").parent
    profile = profiles / "claude-ai-chrome-profile-reg-stalebeef"
    profile.mkdir(parents=True)
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    monkeypatch.setattr(reg.cdp_lane, "chrome_port_for_profile", lambda _p: None)

    with reg._store.ports_lock():
        active = reg._store.load_active()
        active["stale"] = {
            "registration_id": "stale",
            "port": 9224,
            "profile_suffix": "reg-stalebeef",
            "profile": str(profile),
            "holder": "a",
            "status": "orphaned_retry",
        }
        reg._store.write_active(active)

    reg.hygiene_reclaim_released()
    lines = (isolated_registry / "registry.jsonl").read_text(encoding="utf-8").strip().splitlines()
    reclaim_lines = [json.loads(line) for line in lines if json.loads(line)["event"] == "hygiene_reclaim"]
    assert len(reclaim_lines) == 1
    record = reclaim_lines[0]
    assert record["status"] == "reclaimed"
    assert record["profile_removed"] is True
    assert "orphaned_retry" not in record.values()


def test_bind_session_address_survives_release(isolated_registry: Path) -> None:
    r = reg.register_lane(
        holder="a",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    url = "https://claude.ai/cowork/cse_bindTest001"
    assert reg.bind_session_address(r.registration_id, chat_url=url, execution_id="e1")
    assert reg.chat_url_for_registration(r.registration_id) == url
    reg.deregister_lane(r.registration_id, kill=False)
    row = reg._store.load_active()[r.registration_id]
    assert row["status"] == "released"
    assert row["chat_url"] == url
    assert reg.chat_url_for_registration(r.registration_id) == url


def test_backfill_orphaned_retry_chat_urls_verdict(
    isolated_registry: Path,
) -> None:
    with reg._store.ports_lock():
        active = reg._store.load_active()
        active["or-scrape"] = {
            "registration_id": "or-scrape",
            "port": 9223,
            "profile_suffix": "reg-or1",
            "holder": "a",
            "status": "orphaned_retry",
        }
        active["or-empty"] = {
            "registration_id": "or-empty",
            "port": 9224,
            "profile_suffix": "reg-or2",
            "holder": "a",
            "status": "orphaned_retry",
        }
        active["or-bound"] = {
            "registration_id": "or-bound",
            "port": 9225,
            "profile_suffix": "reg-or3",
            "holder": "a",
            "status": "orphaned_retry",
            "chat_url": "https://claude.ai/cowork/cse_alreadyBound",
        }
        reg._store.write_active(active)

    scrape_url = "https://claude.ai/cowork/cse_scraped001"

    def _probe(port: int) -> list[str]:
        if port == 9223:
            return [scrape_url]
        return ["chrome://newtab/"]

    dry = reg.backfill_orphaned_retry_chat_urls(dry_run=True, probe_urls=_probe)
    assert dry["counts"]["already_bound"] == 1
    assert dry["counts"]["scrape_recoverable"] == 1
    assert dry["counts"]["irreversible_no_url"] == 1
    assert dry["counts"]["scrape_bound"] == 0

    wet = reg.backfill_orphaned_retry_chat_urls(dry_run=False, probe_urls=_probe)
    assert wet["counts"]["scrape_bound"] == 1
    assert wet["counts"]["irreversible_no_url"] == 1
    assert reg.chat_url_for_registration("or-scrape") == scrape_url
