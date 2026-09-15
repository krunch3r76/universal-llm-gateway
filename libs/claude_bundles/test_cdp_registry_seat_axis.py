"""Seat-axis write-side: I5 bind, I6 keep, D3 kill refuse, events, ensure, census gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_registry.models import seat_open
from claude_bundles.hop_cadence_seat_snap import (
    seat_row_from_registry_record,
    seated_rows_from_registry_records,
)
from claude_bundles.request_admission_census import census_match_ids

pytestmark = pytest.mark.offline

_LANE = "9498"
_CSE = "https://claude.ai/cowork/cse_seat_axis_test"


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
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
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9228))
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


def _mint_driving(*, holder: str = "seat-test") -> Any:
    return reg.register_lane(
        holder=holder,
        purpose="operator-proxy",
        mission_kind="root",
        parent_thread=_LANE,
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )


def test_i5_second_bind_closes_predecessor_seat(isolated_registry: Path) -> None:
    first = _mint_driving(holder="a")
    assert reg.bind_session_address(first.registration_id, chat_url=_CSE)
    second = _mint_driving(holder="b")
    assert reg.bind_session_address(second.registration_id, chat_url=_CSE + "2")
    active = reg._load_active()
    assert seat_open(active[second.registration_id], _LANE)
    assert not seat_open(active[first.registration_id], _LANE)
    assert active[first.registration_id]["seat_closed_at"] is not None


def test_i6_write_active_keeps_omitted_open_seat(isolated_registry: Path) -> None:
    minted = _mint_driving()
    assert reg.bind_session_address(minted.registration_id, chat_url=_CSE)
    active = reg._load_active()
    dropped = {rid: row for rid, row in active.items() if rid != minted.registration_id}
    reg._store.write_active(dropped)
    kept = reg._load_active()
    assert minted.registration_id in kept
    assert seat_open(kept[minted.registration_id], _LANE)


def test_d3_released_kill_refuses_open_seat(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", lambda port: killed.append(port))
    minted = _mint_driving()
    assert reg.bind_session_address(minted.registration_id, chat_url=_CSE)
    reg.deregister_lane(
        minted.registration_id,
        kill=True,
        is_listening=lambda _p: True,
    )
    assert killed == [minted.port]
    killed.clear()
    row = reg._load_active()[minted.registration_id]
    assert row["status"] == "released"
    assert seat_open(row, _LANE)
    reg.deregister_lane(
        minted.registration_id,
        kill=True,
        is_listening=lambda _p: True,
    )
    assert killed == []
    assert seat_open(reg._load_active()[minted.registration_id], _LANE)


def test_bind_emits_lane_bound_event(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def _capture(event: Any) -> None:
        captured.append(getattr(event, "signal", "") or "")

    monkeypatch.setattr(reg._events, "emit", _capture)
    minted = _mint_driving()
    assert reg.bind_session_address(minted.registration_id, chat_url=_CSE)
    assert "cdp.seat.lane_bound" in captured


def test_ensure_relaunches_dormant_open_seat_not_second_host(
    isolated_registry: Path,
) -> None:
    minted = _mint_driving()
    assert reg.bind_session_address(minted.registration_id, chat_url=_CSE)
    parked = reg.make_dormant(
        minted.registration_id, is_listening=lambda _p: False
    )
    assert parked is not None
    assert seat_open(reg._load_active()[minted.registration_id], _LANE)
    before = set(reg._load_active())
    out = reg.ensure_driving_operator_seat(
        holder="relaunch",
        parent_thread=_LANE,
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    after = set(reg._load_active())
    assert after == before
    assert out.registration_id == minted.registration_id
    assert reg._load_active()[out.registration_id]["status"] == "active"


def test_write_read_gate_open_seat_census_still_zero(isolated_registry: Path) -> None:
    minted = _mint_driving()
    assert reg.bind_session_address(minted.registration_id, chat_url=_CSE)
    with reg._store.ports_lock():
        active = reg._load_active()
        row = dict(active[minted.registration_id])
        row["status"] = "dormant"
        active[minted.registration_id] = row
        reg._store.write_active(active)
    disk = reg._load_active()[minted.registration_id]
    assert seat_open(disk, _LANE)
    seated = seated_rows_from_registry_records(reg._load_active())
    assert seated == []
    snap = {"rows": [], "seated_rows": seated}
    assert census_match_ids(_LANE, snap) == []


def test_convergence_emits_lane_released_per_predecessor(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5: multi-close convergence emits one lane_released per closed row."""
    captured: list[str] = []

    def _capture(event: Any) -> None:
        captured.append(getattr(event, "signal", "") or "")

    monkeypatch.setattr(reg._events, "emit", _capture)
    first = _mint_driving(holder="a")
    second = _mint_driving(holder="b")
    third = _mint_driving(holder="c")
    active = reg._load_active()
    for rid in (first.registration_id, second.registration_id, third.registration_id):
        row = dict(active[rid])
        row["seat_lane"] = _LANE
        row["seat_bound_at"] = float(active[rid].get("seat_bound_at") or 0) + 1.0
        row["seat_closed_at"] = None
        active[rid] = row
    active[third.registration_id]["seat_bound_at"] = 900.0
    reg._store.write_active(active)
    captured.clear()
    reg.bind_driving_seat(third.registration_id)
    released = [s for s in captured if s == "cdp.seat.lane_released"]
    assert len(released) == 2
    assert "cdp.seat.lane_bound" in captured
    assert "cdp.seat.lane_reconciled" in captured


def test_relaunch_dormant_refreshes_seat_bound_at_above_predecessors(
    isolated_registry: Path,
) -> None:
    """AC2: dormant wake + bind yields seat_bound_at above closed predecessors."""
    from claude_bundles.cdp_registry.session_address import apply_driving_seat_bind

    first = _mint_driving(holder="a")
    second = _mint_driving(holder="b")
    active = reg._load_active()
    for rid, bound_at in (
        (first.registration_id, 100.0),
        (second.registration_id, 500.0),
    ):
        row = dict(active[rid])
        row.update(
            {
                "seat_lane": _LANE,
                "seat_bound_at": bound_at,
                "seat_closed_at": None,
            }
        )
        active[rid] = row
    active[first.registration_id]["status"] = "dormant"
    reg._store.write_active(active)
    bound, released = apply_driving_seat_bind(
        reg._load_active(), first.registration_id, now=600.0
    )
    assert bound is not None
    assert len(released) == 1
    assert bound["seat_bound_at"] > 500.0
    assert bound["seat_bound_at"] > released[0]["seat_bound_at"]


def test_ra_seat_row_projector_omits_port_and_cdp_url(isolated_registry: Path) -> None:
    minted = _mint_driving()
    assert reg.bind_session_address(minted.registration_id, chat_url=_CSE)
    active_row = reg._load_active()[minted.registration_id]
    assert "port" in active_row
    projected = seat_row_from_registry_record(active_row)
    assert projected is not None
    assert "port" not in projected
    assert "cdp_url" not in projected


def test_registry_dir_pins_operator_home_under_dispatch_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: overlay HOME resolves registry to operator passwd home."""
    import pwd

    from claude_bundles import cdp_registry_store as store

    operator = tmp_path / "operator-home"
    operator.mkdir()
    dispatch = tmp_path / "cursor-dispatch-homes" / "auto-dispatch-home"
    dispatch.mkdir(parents=True)
    monkeypatch.setattr(
        pwd,
        "getpwuid",
        lambda _uid: type("Pw", (), {"pw_dir": str(operator)})(),
    )
    monkeypatch.setenv("HOME", str(dispatch))
    monkeypatch.delenv("CDP_REGISTRY_HOME", raising=False)
    monkeypatch.delenv("CHARTER_RUNNER_OPERATOR_HOME", raising=False)
    resolved = store._resolve_registry_dir()
    assert resolved == operator / ".gateway" / "cdp-registry"


def test_bind_session_address_journals_seat_superseded(
    isolated_registry: Path,
) -> None:
    """AC2: bind_session_address appends seat_lane_bound with superseded[]."""
    first = _mint_driving(holder="a")
    assert reg.bind_session_address(first.registration_id, chat_url=_CSE)
    second = _mint_driving(holder="b")
    assert reg.bind_session_address(second.registration_id, chat_url=_CSE + "2")
    lines = reg._store.read_registry_log()
    seat_lines = [ln for ln in lines if ln.get("event") == "seat_lane_bound"]
    assert len(seat_lines) >= 2
    last = seat_lines[-1]
    assert last.get("registration_id") == second.registration_id
    assert first.registration_id in (last.get("superseded") or [])


def test_non_authority_seat_write_refused(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3: non-authority seat bind raises ProtocolError."""
    from universal_protocol.errors import ProtocolError

    minted = _mint_driving()
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "0")
    with pytest.raises(ProtocolError, match="authority_refused"):
        reg.bind_session_address(minted.registration_id, chat_url=_CSE)


def test_seat_fold_invariant_at_most_one_open_per_lane(
    isolated_registry: Path,
) -> None:
    """AC5: journal replay never yields >1 open seat per lane."""
    first = _mint_driving(holder="a")
    assert reg.bind_session_address(first.registration_id, chat_url=_CSE)
    second = _mint_driving(holder="b")
    assert reg.bind_session_address(second.registration_id, chat_url=_CSE + "2")
    reg._store.verify_seat_fold_invariant()


def test_cross_home_registry_dir_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6: dispatch overlay and operator HOME resolve the same registry path."""
    import pwd

    from claude_bundles import cdp_registry_store as store

    operator = tmp_path / "operator-home"
    dispatch = tmp_path / "cursor-dispatch-homes" / "auto-x-home"
    dispatch.mkdir(parents=True)
    monkeypatch.setattr(
        pwd,
        "getpwuid",
        lambda _uid: type("Pw", (), {"pw_dir": str(operator)})(),
    )
    monkeypatch.delenv("CDP_REGISTRY_HOME", raising=False)
    monkeypatch.setenv("HOME", str(dispatch))
    from_dispatch = store._resolve_registry_dir()
    monkeypatch.setenv("HOME", str(operator))
    from_operator = store._resolve_registry_dir()
    assert from_dispatch == from_operator
