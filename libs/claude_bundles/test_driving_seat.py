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


def test_ensure_converges_when_two_open_seats_exist(
    isolated_registry: Path,
) -> None:
    """AC1: N>1 seat-open rows converge to one without RegistryError."""
    from claude_bundles.cdp_registry.models import seat_open

    first = _ensure()
    active = reg._store.load_active()
    first_row = dict(active[first.registration_id])
    second_id = "dormant-open-seat"
    second_row = dict(first_row)
    second_row.update(
        {
            "registration_id": second_id,
            "status": "dormant",
            "seat_lane": "9497",
            "seat_bound_at": 100.0,
            "seat_closed_at": None,
            "port": 9224,
            "profile_suffix": "seat-b",
        }
    )
    first_row.update(
        {
            "seat_lane": "9497",
            "seat_bound_at": 500.0,
            "seat_closed_at": None,
        }
    )
    active[first.registration_id] = first_row
    active[second_id] = second_row
    reg._store.write_active(active)
    third = _ensure()
    final = reg._store.load_active()
    open_ids = [
        rid
        for rid, row in final.items()
        if isinstance(row, dict) and seat_open(row, "9497")
    ]
    assert len(open_ids) == 1
    assert third.registration_id == first.registration_id
    assert not seat_open(final[second_id], "9497")


def test_ensure_collapses_ambiguous_listable_rows_to_one_successor(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two listable hosts on a lane retire down to the newest seat."""
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    from claude_bundles.hop_cadence_seat_snap import seated_rows_from_registry_records
    from claude_bundles.request_admission_census import census_match_ids

    first = _ensure()
    reg.register_lane(
        holder="second-root",
        purpose="operator-proxy",
        mission_kind="root",
        parent_thread="9497",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    active = reg._store.load_active()
    active[first.registration_id]["seat_bound_at"] = 50.0
    reg._store.write_active(active)
    winner = _ensure()
    assert winner.registration_id == first.registration_id
    snap = {
        "rows": [],
        "seated_rows": seated_rows_from_registry_records(reg._store.load_active()),
    }
    assert census_match_ids("9497", snap) == [first.registration_id]


def test_hop_bind_retires_stale_registry_rows(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """team_dispatch hop mint leaves one census row on the lane."""
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9230))
    monkeypatch.setattr(reg.cdp_lane, "_launch_chrome", _noop_launch)
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _port: False)
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity.require_chrome_headroom",
        lambda **_kwargs: None,
    )
    from cdp_ask.models import SubmitProjectAskRequest
    from cdp_ask.runner import bind_execution_lane

    from claude_bundles.hop_cadence_seat_snap import seated_rows_from_registry_records
    from claude_bundles.request_admission_census import census_match_ids

    reg.register_lane(
        holder="stale-a",
        purpose="operator-proxy",
        mission_kind="root",
        parent_thread="9497",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.register_lane(
        holder="stale-b",
        purpose="operator-proxy",
        mission_kind="root",
        parent_thread="9497",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    seat = bind_execution_lane(
        SubmitProjectAskRequest(
            prompt_text="x",
            holder="hop",
            purpose="operator-proxy",
            mission_kind="hop",
            parent_thread="9497",
        ),
        holder="hop",
    )
    snap = {
        "rows": [],
        "seated_rows": seated_rows_from_registry_records(reg._store.load_active()),
    }
    assert census_match_ids("9497", snap) == [seat.registration_id]
    # A later non-hop operator-proxy must not reopen a closed predecessor
    # and close the hop successor (G6 revise, execution 07241d05).
    again = _ensure()
    assert again.registration_id == seat.registration_id
    snap_after = {
        "rows": [],
        "seated_rows": seated_rows_from_registry_records(reg._store.load_active()),
    }
    assert census_match_ids("9497", snap_after) == [seat.registration_id]


def test_relaunch_closed_predecessor_refuses_and_leaves_pointer(
    isolated_registry: Path,
) -> None:
    """A dormant predecessor with seat_closed_at must not reopen and steal the seat."""
    hop = reg.register_lane(
        holder="hop-chrome",
        purpose="operator-proxy",
        mission_kind="hop",
        parent_thread="9497",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.bind_driving_seat(hop.registration_id)
    pred = reg.register_lane(
        holder="pred-chrome",
        purpose="operator-proxy",
        mission_kind="root",
        parent_thread="9497",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    active = reg._store.load_active()
    active[pred.registration_id]["status"] = "dormant"
    active[pred.registration_id]["seat_closed_at"] = 1.0
    active[pred.registration_id]["seat_close_reason"] = "superseded"
    reg._store.write_active(active)
    reg._release_driver_lock(pred.registration_id)
    before = dict(reg._store.load_active()[hop.registration_id])

    with pytest.raises(RegistryError, match="seat_closed_at"):
        reg.relaunch_dormant(
            pred.registration_id,
            launch_chrome=_noop_launch,
            is_listening=lambda _p: False,
        )

    after_active = reg._store.load_active()
    assert after_active[hop.registration_id]["seat_lane"] == before["seat_lane"]
    assert after_active[hop.registration_id]["seat_bound_at"] == before["seat_bound_at"]
    assert after_active[hop.registration_id]["seat_closed_at"] is None
    assert after_active[pred.registration_id]["status"] == "dormant"
    assert after_active[pred.registration_id]["seat_closed_at"] == 1.0


def test_relaunch_superseded_during_headroom_refuses_and_leaves_hop(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hop bind inside the headroom window must stick; relaunch must not reopen."""
    hop = reg.register_lane(
        holder="hop-chrome",
        purpose="operator-proxy",
        mission_kind="hop",
        parent_thread="9497",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.bind_driving_seat(hop.registration_id)
    pred = reg.register_lane(
        holder="pred-chrome",
        purpose="operator-proxy",
        mission_kind="root",
        parent_thread="9497",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    active = reg._store.load_active()
    active[pred.registration_id]["status"] = "dormant"
    active[pred.registration_id]["seat_lane"] = "9497"
    active[pred.registration_id]["seat_bound_at"] = 1.0
    active[pred.registration_id]["seat_closed_at"] = None
    active[hop.registration_id]["seat_closed_at"] = 1.0
    reg._store.write_active(active)
    reg._release_driver_lock(pred.registration_id)
    pointer: dict[str, object] = {}
    calls = {"n": 0}

    def close_during_headroom(**_kwargs: object) -> None:
        calls["n"] += 1
        reg.bind_driving_seat(hop.registration_id)
        pointer.update(reg._store.load_active()[hop.registration_id])

    monkeypatch.setattr(
        "claude_bundles.x_display_capacity.require_chrome_headroom",
        close_during_headroom,
    )

    try:
        reg.relaunch_dormant(
            pred.registration_id,
            launch_chrome=_noop_launch,
            is_listening=lambda _p: False,
        )
    except RegistryError:
        pass
    else:
        after_dbg = reg._store.load_active()
        raise AssertionError(
            f"calls={calls} pred={after_dbg[pred.registration_id].get('seat_closed_at')} "
            f"hop_closed={after_dbg[hop.registration_id].get('seat_closed_at')}"
        )

    after = reg._store.load_active()
    hop_row = after[hop.registration_id]
    assert hop_row["seat_lane"] == pointer["seat_lane"]
    assert hop_row["seat_bound_at"] == pointer["seat_bound_at"]
    assert hop_row["seat_closed_at"] is None
    assert after[pred.registration_id]["seat_closed_at"] is not None


def test_ensure_rejects_hop_as_driving_kind(isolated_registry: Path) -> None:
    with pytest.raises(RegistryError, match="cannot be mission_kind=hop"):
        _ensure(mission_kind="hop")


def test_ensure_recensus_second_seat_contended_propagates(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claude_bundles.cdp_registry.models import SeatContended

    attempts = {"n": 0}

    def always_contended(*_args: object, **_kwargs: object) -> object:
        attempts["n"] += 1
        raise SeatContended(
            "still contended",
            retryable=True,
            data={
                "depth": "pre_lock_status_check",
                "observed_status": "allocating",
                "registration_id": "rid",
            },
        )

    monkeypatch.setattr(reg, "relaunch_dormant", always_contended)
    first = _ensure()
    active = reg._store.load_active()
    active[first.registration_id]["status"] = "dormant"
    active[first.registration_id]["seat_lane"] = "9497"
    active[first.registration_id]["seat_bound_at"] = 1.0
    active[first.registration_id]["seat_closed_at"] = None
    reg._store.write_active(active)
    with pytest.raises(SeatContended, match="still contended"):
        _ensure()
    assert attempts["n"] == 2


def test_ensure_recensus_r1_allocating_open_seat_bind_without_relaunch(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Winner reserve between census and bind: retry binds open seat, no relaunch."""
    from claude_bundles.cdp_registry.models import seat_open

    first = _ensure()
    active = reg._store.load_active()
    active[first.registration_id]["status"] = "dormant"
    active[first.registration_id]["seat_lane"] = None
    active[first.registration_id]["seat_bound_at"] = None
    active[first.registration_id]["seat_closed_at"] = None
    reg._store.write_active(active)
    reg._release_driver_lock(first.registration_id)

    real_bind = reg.bind_driving_seat
    bind_pass = {"n": 0}
    relaunch_calls: list[str] = []
    real_relaunch = reg.relaunch_dormant

    def bind_then_allocate(rid: str) -> None:
        bind_pass["n"] += 1
        if bind_pass["n"] == 1:
            row = dict(reg._store.load_active()[rid])
            row["status"] = "allocating"
            for key in ("seat_lane", "seat_bound_at", "seat_closed_at"):
                row.pop(key, None)
            active_now = reg._store.load_active()
            active_now[rid] = row
            reg._store.write_active(active_now)
        real_bind(rid)

    def track_relaunch(registration_id: str, **kwargs: object) -> object:
        outcome = real_relaunch(registration_id, **kwargs)  # type: ignore[arg-type]
        relaunch_calls.append(registration_id)
        return outcome

    monkeypatch.setattr(reg, "bind_driving_seat", bind_then_allocate)
    monkeypatch.setattr(reg, "relaunch_dormant", track_relaunch)

    again = _ensure()
    assert again.registration_id == first.registration_id
    row = reg._store.load_active()[again.registration_id]
    assert row.get("status") == "allocating"
    assert seat_open(row, "9497")
    assert relaunch_calls == []


def test_ensure_emits_recensus_joined_only_on_recovered_retry(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claude_bundles import cdp_registry_events as ev

    emitted: list[str] = []
    real_emit = ev.emit

    def capture_emit(event: object) -> None:
        sig = getattr(event, "signal", None)
        if sig:
            emitted.append(str(sig))
        real_emit(event)

    monkeypatch.setattr(ev, "emit", capture_emit)

    first = _ensure()
    assert "cdp.seat.recensus_joined" not in emitted

    active = reg._store.load_active()
    active[first.registration_id]["status"] = "dormant"
    active[first.registration_id]["seat_lane"] = "9497"
    active[first.registration_id]["seat_bound_at"] = 10.0
    active[first.registration_id]["seat_closed_at"] = None
    reg._store.write_active(active)

    real_relaunch = reg.relaunch_dormant
    calls = {"n": 0}

    def fail_once_then_relaunch(registration_id: str, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            reg._release_driver_lock(registration_id)
            raise reg.SeatContended(
                f"registration {registration_id!r} is 'allocating', not dormant",
                retryable=True,
                data={
                    "depth": "pre_lock_status_check",
                    "observed_status": "allocating",
                    "registration_id": registration_id,
                },
            )
        return real_relaunch(registration_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(reg, "relaunch_dormant", fail_once_then_relaunch)
    _ensure()
    assert emitted.count("cdp.seat.recensus_joined") == 1


def test_ensure_recensus_register_lane_join_when_no_bound_seat(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contended retry with no bound seat joins the allocating row."""
    from claude_bundles import cdp_registry_events as ev
    from claude_bundles.cdp_registry import driving_seat as seat_mod
    from claude_bundles.cdp_registry import lifecycle as life

    seeded_id = "alloc-join-9497"
    reg._store.write_active(
        {
            seeded_id: {
                "registration_id": seeded_id,
                "port": 9223,
                "profile_suffix": "reg-allocjoin",
                "profile": str(isolated_registry / "profile"),
                "holder": "operator-seat",
                "purpose": "operator-proxy",
                "mission_kind": "root",
                "parent_thread": "9497",
                "status": "allocating",
                "chrome_pid": None,
                "holder_pid": None,
                "started_at": 1.0,
            }
        }
    )
    reg._claim_driver_lock(seeded_id)

    real_census = seat_mod.driving_lane_census
    census_calls = {"n": 0}
    retry_snaps: list[dict[str, list]] = []

    def fail_first_census(active_map: object, parent: str) -> object:
        census_calls["n"] += 1
        if census_calls["n"] == 1:
            raise reg.SeatContended(
                "pre-lock status check contended",
                retryable=True,
                data={
                    "depth": "pre_lock_status_check",
                    "observed_status": "allocating",
                    "registration_id": seeded_id,
                },
            )
        snap = real_census(active_map, parent)  # type: ignore[arg-type]
        retry_snaps.append(snap)
        return snap

    monkeypatch.setattr(seat_mod, "driving_lane_census", fail_first_census)

    emitted: list[str] = []
    joined_events: list[object] = []
    real_emit = ev.emit

    def capture_emit(event: object) -> None:
        sig = getattr(event, "signal", None)
        if sig:
            emitted.append(str(sig))
        if sig == "cdp.seat.recensus_joined":
            joined_events.append(event)
        real_emit(event)

    monkeypatch.setattr(ev, "emit", capture_emit)

    reserve_calls: list[dict[str, object]] = []
    real_reserve = life.reserve_allocating_row

    def spy_reserve(**kwargs: object) -> object:
        reserve_calls.append(dict(kwargs))
        return real_reserve(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(life, "reserve_allocating_row", spy_reserve)

    def promote_joined(registration_id: str) -> None:
        current = reg._store.load_active()
        row = dict(current[registration_id])
        row["status"] = "active"
        current[registration_id] = row
        reg._store.write_active(current)

    monkeypatch.setattr(life, "_between_wait_observations", promote_joined)

    joined: list[str] = []
    result = _ensure(joined=joined)

    assert census_calls["n"] >= 2
    assert retry_snaps
    assert all(len(snap["open_seats"]) == 0 for snap in retry_snaps)
    assert all(len(snap["dormant_unbound"]) == 0 for snap in retry_snaps)
    assert all(len(snap["live"]) == 0 for snap in retry_snaps)
    assert result.registration_id == seeded_id
    assert joined == [seeded_id]
    assert emitted.count("cdp.seat.recensus_joined") == 1
    payload = getattr(joined_events[0], "payload")
    assert payload["branch"] == "register_lane_join"
    assert reserve_calls
    assert reserve_calls[0]["join"] is True
    assert reserve_calls[0].get("registration_id") is None
