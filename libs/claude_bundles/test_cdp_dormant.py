"""Dormant CSE seat lifecycle — park, protect, relaunch, drain, reclaim."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_registry.dormant_drain import drain_live_hosts_to_dormant

pytestmark = pytest.mark.offline


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    for name, value in {
        "REGISTRY_DIR": root,
        "REGISTRY_LOG": root / "registry.jsonl",
        "ACTIVE_JSON": root / "active.json",
        "SESSIONS_JSON": root / "sessions.json",
        "SESSION_TRANSITIONS_JSONL": root / "session_transitions.jsonl",
        "PORTS_LOCK": root / "ports.lock",
        "REGISTRATIONS_DIR": regs,
    }.items():
        monkeypatch.setattr(reg._store, name, value)
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9228))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    monkeypatch.setattr(reg.cdp_lane, "PRIMARY_PROFILE", profiles / "primary")
    (profiles / "primary").mkdir()
    return root


def _noop_launch(port: int, profile: Path) -> int:
    profile.mkdir(parents=True, exist_ok=True)
    return 4242


def _seat(purpose: str = "operator-proxy", chat_url: str | None = None) -> Any:
    reg_row = reg.register_lane(
        holder="dormancy-test",
        purpose=purpose,
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    if chat_url:
        reg.bind_session_address(reg_row.registration_id, chat_url=chat_url)
    # register_lane claims the driver flock; a parked seat has no driver.
    reg._release_driver_lock(reg_row.registration_id)
    return reg_row


def _row(registration_id: str) -> dict[str, Any]:
    return reg._load_active()[registration_id]


def test_make_dormant_frees_port_and_keeps_identity(isolated_registry: Path) -> None:
    url = "https://claude.ai/cowork/cse_park1"
    seat = _seat(chat_url=url)
    parked = reg.make_dormant(
        seat.registration_id,
        is_listening=lambda _p: True,
    )
    assert parked is not None
    assert parked.chat_url == url
    row = _row(seat.registration_id)
    assert row["status"] == "dormant"
    assert row["chrome_pid"] is None
    assert row["chat_url"] == url
    # Profile survives so relaunch is a Chrome start, not a profile copy.
    assert parked.profile.exists()
    # The freed port is available to the next allocation.
    assert seat.port not in reg.used_ports_snapshot()


def test_make_dormant_kills_the_listener(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seat = _seat(chat_url="https://claude.ai/cowork/cse_kill")
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", killed.append)

    assert reg.make_dormant(seat.registration_id, is_listening=lambda _p: True)
    assert killed == [seat.port]


def test_make_dormant_refuses_without_chat_url(isolated_registry: Path) -> None:
    seat = _seat()
    assert reg.make_dormant(seat.registration_id) is None
    assert _row(seat.registration_id)["status"] == "active"


def test_make_dormant_refuses_while_driver_attached(isolated_registry: Path) -> None:
    reg_row = reg.register_lane(
        holder="locked",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    seat = reg_row
    reg.bind_session_address(seat.registration_id, chat_url="https://cse_locked")
    # Simulate a peer driver: drop the in-process record, keep the flock held.
    reg._HELD_LOCKS.pop(seat.registration_id, None)
    assert reg.make_dormant(seat.registration_id) is None
    assert _row(seat.registration_id)["status"] == "active"


def test_make_dormant_refuses_on_wake_debt(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seat = _seat(chat_url="https://claude.ai/cowork/cse_debt")
    monkeypatch.setattr(
        "claude_bundles.cse_wake_retain.registration_has_wake_debt",
        lambda _rid: True,
    )
    assert reg.make_dormant(seat.registration_id) is None
    assert _row(seat.registration_id)["status"] == "active"


def test_deregister_dormant_row_never_kills_a_reused_port(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seat = _seat(chat_url="https://claude.ai/cowork/cse_stale_port")
    reg.make_dormant(seat.registration_id, is_listening=lambda _p: False)
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", killed.append)

    reg.deregister_lane(seat.registration_id, kill=True)
    assert killed == []
    assert _row(seat.registration_id)["status"] == "dormant"


def test_relaunch_dormant_reuses_profile_and_binding(isolated_registry: Path) -> None:
    url = "https://claude.ai/cowork/cse_wake"
    seat = _seat(chat_url=url)
    parked = reg.make_dormant(seat.registration_id, is_listening=lambda _p: False)
    assert parked is not None

    woken = reg.relaunch_dormant(
        seat.registration_id,
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert woken.registration_id == seat.registration_id
    assert woken.profile_suffix == seat.profile_suffix
    row = _row(seat.registration_id)
    assert row["status"] == "active"
    assert row["chat_url"] == url
    assert reg.chat_url_for_registration(seat.registration_id) == url


def test_relaunch_failure_returns_the_seat_to_dormant(isolated_registry: Path) -> None:
    seat = _seat(chat_url="https://claude.ai/cowork/cse_fail")
    reg.make_dormant(seat.registration_id, is_listening=lambda _p: False)

    def _boom(port: int, profile: Path) -> int:
        raise RuntimeError("chrome refused to start")

    with pytest.raises(RuntimeError):
        reg.relaunch_dormant(
            seat.registration_id,
            launch_chrome=_boom,
            is_listening=lambda _p: False,
        )
    row = _row(seat.registration_id)
    assert row["status"] == "dormant"
    assert row["chat_url"] == "https://claude.ai/cowork/cse_fail"


def test_dormant_for_chat_url_prefers_the_newest_binding(
    isolated_registry: Path,
) -> None:
    url = "https://claude.ai/cowork/cse_dup"
    older = _seat(chat_url=url)
    reg.make_dormant(older.registration_id, is_listening=lambda _p: False)
    newer = _seat(chat_url=url)
    reg.make_dormant(newer.registration_id, is_listening=lambda _p: False)

    found = reg.dormant_for_chat_url(url)
    assert found is not None
    assert found.registration_id == newer.registration_id


def test_reclaim_dormant_rows_by_ttl_and_cap(isolated_registry: Path) -> None:
    ids: list[str] = []
    for index in range(3):
        seat = _seat(chat_url=f"https://claude.ai/cowork/cse_ttl{index}")
        reg.make_dormant(seat.registration_id, is_listening=lambda _p: False)
        ids.append(seat.registration_id)

    assert reg.reclaim_dormant_rows(ttl_s=3600, max_rows=8) == []

    over_cap = reg.reclaim_dormant_rows(ttl_s=3600, max_rows=1)
    assert len(over_cap) == 2
    assert all(_row(rid)["status"] == "released" for rid in over_cap)

    aged = reg.reclaim_dormant_rows(ttl_s=0, max_rows=8)
    assert len(aged) == 1


def test_drain_parks_retained_hosts_and_protects_the_busy_one(
    isolated_registry: Path,
) -> None:
    parked_seat = _seat(chat_url="https://claude.ai/cowork/cse_drain1")
    busy_seat = _seat(chat_url="https://claude.ai/cowork/cse_drain2")
    for seat in (parked_seat, busy_seat):
        reg.deregister_lane(seat.registration_id, kill=False, reason="retained")

    result = drain_live_hosts_to_dormant(
        is_listening=lambda _p: False,
        is_busy=lambda rid: rid == busy_seat.registration_id,
    )
    assert result.dormant == [parked_seat.registration_id]
    assert result.protected[busy_seat.registration_id] == "paste_in_flight"
    assert _row(parked_seat.registration_id)["status"] == "dormant"
    assert _row(busy_seat.registration_id)["status"] == "retained"


def test_drain_keeps_streaming_cse_open_for_monitoring(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live stream pins its page until the liveness probe reports idle."""
    url = "https://claude.ai/cowork/cse_streaming"
    seat = _seat(chat_url=url)
    reg.deregister_lane(seat.registration_id, kill=False, reason="retained")
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_drain.cdp_orphans._fetch_json",
        lambda _url: [
            {
                "type": "page",
                "url": url,
                "webSocketDebuggerUrl": "ws://streaming",
            }
        ],
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_drain.probe_page_liveness_sync",
        lambda _port, _websocket: (
            {"streaming": True, "stop": False, "tool_pause": False},
            True,
        ),
    )

    result = drain_live_hosts_to_dormant(
        is_listening=lambda _p: True,
    )
    assert result.dormant == []
    assert result.protected[seat.registration_id] == "streaming_monitoring"
    assert _row(seat.registration_id)["status"] == "retained"


def test_drain_releases_monitoring_lease_when_stream_stops(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An idle liveness probe permits immediate parking on the next sweep."""
    url = "https://claude.ai/cowork/cse_stream_stopped"
    seat = _seat(chat_url=url)
    reg.deregister_lane(seat.registration_id, kill=False, reason="retained")
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_drain.cdp_orphans._fetch_json",
        lambda _url: [
            {
                "type": "page",
                "url": url,
                "webSocketDebuggerUrl": "ws://idle",
            }
        ],
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_drain.probe_page_liveness_sync",
        lambda _port, _websocket: (
            {"streaming": False, "stop": False, "tool_pause": False},
            True,
        ),
    )

    result = drain_live_hosts_to_dormant(
        is_listening=lambda _p: True,
    )
    assert result.dormant == [seat.registration_id]
    assert _row(seat.registration_id)["status"] == "dormant"


def test_drain_fails_closed_when_stream_probe_is_unavailable(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An attached page is not killed while its monitoring state is unknown."""
    seat = _seat(chat_url="https://claude.ai/cowork/cse_probe_gap")
    reg.deregister_lane(seat.registration_id, kill=False, reason="retained")
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_drain.cdp_orphans._fetch_json",
        lambda _url: None,
    )

    result = drain_live_hosts_to_dormant(
        is_listening=lambda _p: True,
    )
    assert result.dormant == []
    assert result.protected[seat.registration_id] == "stream_probe_unavailable"
    assert _row(seat.registration_id)["status"] == "retained"


def test_drain_releases_a_host_holding_no_session(isolated_registry: Path) -> None:
    seat = _seat()
    reg.deregister_lane(seat.registration_id, kill=False, reason="retained")

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: False)
    assert result.released == [seat.registration_id]
    assert _row(seat.registration_id)["status"] == "released"


def test_boot_adopted_host_is_drainable(isolated_registry: Path) -> None:
    """A host that survives a cdp_ask restart must not become an undrainable seat."""
    from claude_bundles import boot_lane_readoption as blr

    url = "https://claude.ai/cowork/cse_boot"
    seat = _seat(chat_url=url)
    blr.boot_adopt_lane(
        seat.registration_id, prior_status="active", cse_affinity="bound_present"
    )
    assert _row(seat.registration_id)["status"] == "retained"
    assert not reg.is_driver_lock_held(seat.registration_id)

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: False)
    assert result.dormant == [seat.registration_id]
    assert _row(seat.registration_id)["chat_url"] == url


def test_drain_binds_a_probed_url_before_parking(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://claude.ai/cowork/cse_probed"
    seat = _seat()
    reg.deregister_lane(seat.registration_id, kill=False, reason="retained")
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.session_address._default_probe_page_urls",
        lambda _port: [url],
    )

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: False)
    assert result.dormant == [seat.registration_id]
    assert _row(seat.registration_id)["chat_url"] == url
