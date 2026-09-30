"""Dormant CSE seat lifecycle — park, protect, relaunch, drain, reclaim."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_registry import dormant_drain
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


def _successful_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Probe succeeded and found no CSE page — the drainable signal-source case."""
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_drain.cdp_orphans._fetch_json",
        lambda _url: [],
    )


def test_drain_parks_retained_hosts_and_protects_the_busy_one(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parked_seat = _seat(chat_url="https://claude.ai/cowork/cse_drain1")
    busy_seat = _seat(chat_url="https://claude.ai/cowork/cse_drain2")
    for seat in (parked_seat, busy_seat):
        reg.deregister_lane(seat.registration_id, kill=False, reason="retained")
    _successful_empty_list(monkeypatch)

    result = drain_live_hosts_to_dormant(
        is_listening=lambda _p: True,
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


def test_drain_protects_idle_operator_proxy_with_reachable_page(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Harvest-triple idle is not a drain warrant for an operator-proxy seat."""
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
    assert result.dormant == []
    assert result.protected[seat.registration_id] == "reachable_operator_seat"
    assert _row(seat.registration_id)["status"] == "retained"


def test_drain_parks_idle_ask_host_when_stream_stops(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One-shot ask hosts still park when the harvest triple is idle."""
    url = "https://claude.ai/cowork/cse_ask_idle"
    seat = _seat(purpose="ask", chat_url=url)
    reg.deregister_lane(seat.registration_id, kill=False, reason="retained")
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_drain.cdp_orphans._fetch_json",
        lambda _url: [
            {
                "type": "page",
                "url": url,
                "webSocketDebuggerUrl": "ws://ask-idle",
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


def test_drain_protects_idle_when_purpose_signal_absent(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blank purpose fail-closes to protect — absence of the signal is not death."""
    url = "https://claude.ai/cowork/cse_blank_purpose"
    seat = _seat(chat_url=url)
    reg.deregister_lane(seat.registration_id, kill=False, reason="retained")
    active = reg._load_active()
    row = dict(active[seat.registration_id])
    row["purpose"] = ""
    active[seat.registration_id] = row
    reg._store.write_active(active)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_drain.cdp_orphans._fetch_json",
        lambda _url: [
            {
                "type": "page",
                "url": url,
                "webSocketDebuggerUrl": "ws://blank-purpose",
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
    assert result.dormant == []
    assert result.protected[seat.registration_id] == "reachable_operator_seat"
    assert _row(seat.registration_id)["status"] == "retained"


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


def test_orphan_reaper_respects_streaming_monitoring_lease(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legacy orphan TTL path must not bypass the stream safety rule."""
    url = "https://claude.ai/cowork/cse_orphan_streaming"
    seat = _seat(chat_url=url)
    active = reg._load_active()
    row = dict(active[seat.registration_id])
    row.update({"status": "orphaned_alive", "orphaned_at": 0.0, "chrome_pid": 1})
    active[seat.registration_id] = row
    reg._store.write_active(active)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_drain.cdp_orphans._fetch_json",
        lambda _url: [
            {
                "type": "page",
                "url": url,
                "webSocketDebuggerUrl": "ws://orphan-streaming",
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

    reg.hygiene_reclaim_extended(
        is_listening=lambda _p: True,
        empty_trash=False,
    )
    assert _row(seat.registration_id)["status"] == "orphaned_alive"


def test_drain_protects_process_driver_lock_without_cse_page(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model selection holds this process's driver lock before any CSE page exists."""
    seat = reg.register_lane(
        holder="model-select",
        purpose="ask",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert _row(seat.registration_id)["status"] == "active"
    assert not str(_row(seat.registration_id).get("chat_url") or "").strip()
    assert reg.process_holds_driver_lock(seat.registration_id)
    _successful_empty_list(monkeypatch)

    held = drain_live_hosts_to_dormant(is_listening=lambda _p: True)
    assert seat.registration_id not in held.released
    assert held.protected[seat.registration_id] == "process_driver_lock"

    reg._release_driver_lock(seat.registration_id)
    assert not reg.process_holds_driver_lock(seat.registration_id)
    assert not reg.is_driver_lock_held(seat.registration_id)

    cleared = drain_live_hosts_to_dormant(is_listening=lambda _p: True)
    assert seat.registration_id in cleared.released


def test_drain_releases_a_host_holding_no_session(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seat = _seat()
    reg.deregister_lane(seat.registration_id, kill=False, reason="retained")
    _successful_empty_list(monkeypatch)

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: True)
    assert result.released == [seat.registration_id]
    assert _row(seat.registration_id)["status"] == "released"


def test_drain_protects_when_port_is_unreachable(
    isolated_registry: Path,
) -> None:
    """A silent CDP port is signal-source failure, not 'no operator seated'."""
    seat = _seat(chat_url="https://claude.ai/cowork/cse_port_down")
    reg.deregister_lane(seat.registration_id, kill=False, reason="retained")

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: False)
    assert result.dormant == []
    assert result.protected[seat.registration_id] == "cdp_port_unreachable"
    assert _row(seat.registration_id)["status"] == "retained"


def test_drain_protects_when_list_is_unparseable(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-list /json/list body is probe failure, not an empty CSE set."""
    seat = _seat(chat_url="https://claude.ai/cowork/cse_list_junk")
    reg.deregister_lane(seat.registration_id, kill=False, reason="retained")
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_drain.cdp_orphans._fetch_json",
        lambda _url: {"error": "not a list"},
    )

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: True)
    assert result.dormant == []
    assert result.protected[seat.registration_id] == "cdp_list_unparseable"
    assert _row(seat.registration_id)["status"] == "retained"


def test_drain_parks_when_probe_succeeds_with_empty_list(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful empty list is the drainable counterpart of probe failure."""
    seat = _seat(chat_url="https://claude.ai/cowork/cse_empty_ok")
    reg.deregister_lane(seat.registration_id, kill=False, reason="retained")
    _successful_empty_list(monkeypatch)

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: True)
    assert result.dormant == [seat.registration_id]
    assert seat.registration_id not in result.protected
    assert _row(seat.registration_id)["status"] == "dormant"


def test_boot_adopt_preserves_active_for_reachable_operator(
    isolated_registry: Path,
) -> None:
    """A listening operator CSE must not become drainable on cdp_ask restart."""
    from claude_bundles import boot_lane_readoption as blr

    url = "https://claude.ai/cowork/cse_boot_keep"
    seat = _seat(chat_url=url)
    blr.boot_adopt_lane(
        seat.registration_id, prior_status="active", cse_affinity="bound_present"
    )
    assert _row(seat.registration_id)["status"] == "active"
    assert not reg.is_driver_lock_held(seat.registration_id)

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: False)
    assert result.dormant == []
    # "active" is now in-scope for the sweep (previously skipped by status
    # alone); an unreachable port still fails closed to "protected", so the
    # row is never wrongly dormanted either way.
    assert result.protected[seat.registration_id] == "cdp_port_unreachable"
    assert _row(seat.registration_id)["status"] == "active"
    assert _row(seat.registration_id)["chat_url"] == url


def test_boot_adopted_ask_host_is_drainable(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ask hosts stay retained on adopt so idle leaked Chromes can still park."""
    from claude_bundles import boot_lane_readoption as blr

    url = "https://claude.ai/cowork/cse_boot_ask"
    seat = _seat(purpose="ask", chat_url=url)
    blr.boot_adopt_lane(
        seat.registration_id, prior_status="active", cse_affinity="bound_present"
    )
    assert _row(seat.registration_id)["status"] == "retained"
    assert not reg.is_driver_lock_held(seat.registration_id)
    _successful_empty_list(monkeypatch)

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: True)
    assert result.dormant == [seat.registration_id]
    assert _row(seat.registration_id)["chat_url"] == url


def test_boot_adopted_host_without_cse_is_drainable(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No reachable CSE on adopt stays retained — leaked Chrome release depends on it."""
    from claude_bundles import boot_lane_readoption as blr

    url = "https://claude.ai/cowork/cse_boot_none"
    seat = _seat(chat_url=url)
    blr.boot_adopt_lane(
        seat.registration_id, prior_status="active", cse_affinity="none"
    )
    assert _row(seat.registration_id)["status"] == "retained"
    _successful_empty_list(monkeypatch)

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: True)
    assert result.dormant == [seat.registration_id]
    assert _row(seat.registration_id)["chat_url"] == url


def test_drain_parks_idle_active_operator_proxy_past_grace_window(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain-``active`` operator-proxy seat, idle past the grace window, drains.

    Before this change, ``_DRAINABLE_STATUSES`` excluded ``active`` entirely —
    a seat that never cycled through dormancy had no path into this sweep.
    """
    url = "https://claude.ai/cowork/cse_active_stale"
    seat = _seat(chat_url=url)
    # No deregister_lane call — the row stays plain "active", not "retained".
    assert _row(seat.registration_id)["status"] == "active"
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_drain.cdp_orphans._fetch_json",
        lambda _url: [
            {
                "type": "page",
                "url": url,
                "webSocketDebuggerUrl": "ws://active-stale",
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
    started_at = _row(seat.registration_id)["started_at"]
    past_grace = started_at + dormant_drain.operator_idle_grace_s() + 1.0

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: True, now=past_grace)
    assert result.dormant == [seat.registration_id]
    assert _row(seat.registration_id)["status"] == "dormant"


def test_drain_protects_active_operator_proxy_within_grace_window(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same shape, checked before the grace window elapses, is untouched."""
    url = "https://claude.ai/cowork/cse_active_hot"
    seat = _seat(chat_url=url)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_drain.cdp_orphans._fetch_json",
        lambda _url: [
            {
                "type": "page",
                "url": url,
                "webSocketDebuggerUrl": "ws://active-hot",
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
    started_at = _row(seat.registration_id)["started_at"]
    within_grace = started_at + 5.0

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: True, now=within_grace)
    assert result.dormant == []
    assert result.protected[seat.registration_id] == "reachable_operator_seat"
    assert _row(seat.registration_id)["status"] == "active"


def test_drain_protects_streaming_active_operator_proxy_regardless_of_age(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuinely streaming seat is protected even with an ancient ``started_at``."""
    url = "https://claude.ai/cowork/cse_active_streaming_old"
    seat = _seat(chat_url=url)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_drain.cdp_orphans._fetch_json",
        lambda _url: [
            {
                "type": "page",
                "url": url,
                "webSocketDebuggerUrl": "ws://active-streaming-old",
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
    started_at = _row(seat.registration_id)["started_at"]
    far_future = started_at + dormant_drain.operator_idle_grace_s() * 10

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: True, now=far_future)
    assert result.dormant == []
    assert result.protected[seat.registration_id] == "streaming_monitoring"
    assert _row(seat.registration_id)["status"] == "active"


def test_idle_reachable_protects_fails_closed_with_no_timestamp(
    isolated_registry: Path,
) -> None:
    """A live row with no lifecycle timestamp at all protects rather than guesses."""
    row: dict[str, object] = {"purpose": "operator-proxy"}
    assert dormant_drain._idle_reachable_protects(row, now=1_000_000.0) is True


def test_drain_binds_a_probed_url_before_parking(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://claude.ai/cowork/cse_probed"
    seat = _seat()
    reg.deregister_lane(seat.registration_id, kill=False, reason="retained")
    _successful_empty_list(monkeypatch)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.session_address._default_probe_page_urls",
        lambda _port: [url],
    )

    result = drain_live_hosts_to_dormant(is_listening=lambda _p: True)
    assert result.dormant == [seat.registration_id]
    assert _row(seat.registration_id)["chat_url"] == url


def test_dormant_double_relaunch_one_proceeds(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seat = _seat(chat_url="https://claude.ai/cowork/cse_race")
    reg.make_dormant(seat.registration_id, is_listening=lambda _p: False)
    before = 0
    log_path = isolated_registry / "registry.jsonl"
    if log_path.is_file():
        before = sum(
            1
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("event") == "allocating"
        )
    barrier = threading.Barrier(2)
    workers: set[int] = set()
    seen: set[int] = set()
    guard = threading.Lock()
    real_load = reg._store.load_active

    def wrapped() -> dict[str, Any]:
        data = real_load()
        ident = threading.get_ident()
        if ident not in workers:
            return data
        with guard:
            first = ident not in seen
            if first:
                seen.add(ident)
        if first:
            row = data.get(seat.registration_id) or {}
            assert row.get("status") == "dormant"
            barrier.wait(timeout=5)
        return data

    monkeypatch.setattr(reg._store, "load_active", wrapped)
    outcomes: list[tuple[str, object]] = []

    def run() -> None:
        workers.add(threading.get_ident())
        try:
            woken = reg.relaunch_dormant(
                seat.registration_id,
                launch_chrome=_noop_launch,
                is_listening=lambda _port: False,
            )
            outcomes.append(("ok", woken.registration_id))
        except Exception as exc:
            outcomes.append(("err", exc))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    oks = [item for item in outcomes if item[0] == "ok"]
    errs = [item for item in outcomes if item[0] == "err"]
    assert len(oks) == 1
    assert oks[0][1] == seat.registration_id
    assert len(errs) == 1
    assert type(errs[0][1]) is reg.SeatContended
    after = sum(
        1
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("event") == "allocating"
    )
    assert after - before == 1
    err = errs[0][1]
    assert err.retryable is True
    assert err.data["depth"] == "reserve_compare_and_set"


def test_make_dormant_refuses_allocating_row_during_relaunch(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A launch_fn that parks the in-flight row must not steal the relaunch lock."""
    url = "https://claude.ai/cowork/cse_alloc_refuse"
    seat = _seat(chat_url=url)
    reg.make_dormant(seat.registration_id, is_listening=lambda _p: False)
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", killed.append)
    during: dict[str, Any] = {}

    def launch(port: int, profile: Path) -> int:
        assert reg.process_holds_driver_lock(seat.registration_id)
        during["refused"] = reg.make_dormant(
            seat.registration_id, is_listening=lambda _p: True
        )
        during["status"] = _row(seat.registration_id)["status"]
        during["lock"] = reg.process_holds_driver_lock(seat.registration_id)
        profile.mkdir(parents=True, exist_ok=True)
        return 4242

    woken = reg.relaunch_dormant(
        seat.registration_id,
        launch_chrome=launch,
        is_listening=lambda _p: False,
    )
    assert during["refused"] is None
    assert during["status"] == "allocating"
    assert during["lock"] is True
    assert killed == []
    assert woken.registration_id == seat.registration_id
    assert _row(seat.registration_id)["status"] == "active"


def test_make_dormant_refuses_allocating_on_locked_reread(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-lock active snapshot; the locked re-read is already allocating.

    A dormant pre-lock snapshot returns ``already_dormant`` before ``ports.lock``
    and would pass on unfixed code. The snapshot that reaches the lock is a
    lock-free active row that still has ``chat_url``.
    """
    url = "https://claude.ai/cowork/cse_locked_reread"
    seat = _seat(chat_url=url)
    prior = _row(seat.registration_id)
    assert prior["status"] == "active"
    assert str(prior.get("chat_url") or "").strip()
    assert not reg.process_holds_driver_lock(seat.registration_id)
    assert (
        reg.dormant_candidate_reason(prior, registration_id=seat.registration_id)
        is None
    )
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", killed.append)
    real_load = reg._store.load_active
    calls = {"n": 0}

    def wrapped() -> dict[str, Any]:
        data = real_load()
        calls["n"] += 1
        if calls["n"] != 1:
            return data
        snapshot = {
            rid: (dict(row) if isinstance(row, dict) else row)
            for rid, row in data.items()
        }
        reg._claim_driver_lock(seat.registration_id)
        stored = {
            rid: (dict(row) if isinstance(row, dict) else row)
            for rid, row in data.items()
        }
        stored[seat.registration_id]["status"] = "allocating"
        reg._store.write_active(stored)
        return snapshot

    monkeypatch.setattr(reg._store, "load_active", wrapped)
    assert (
        reg.make_dormant(seat.registration_id, is_listening=lambda _p: True) is None
    )
    assert calls["n"] >= 2
    assert killed == []
    assert _row(seat.registration_id)["status"] == "allocating"
    assert reg.process_holds_driver_lock(seat.registration_id)


def test_make_dormant_parks_active_row_held_by_this_process(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-process holder of an active seat can still park it."""
    url = "https://claude.ai/cowork/cse_owner_park"
    seat = reg.register_lane(
        holder="owner",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.bind_session_address(seat.registration_id, chat_url=url)
    assert reg.process_holds_driver_lock(seat.registration_id)
    assert _row(seat.registration_id)["status"] == "active"
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", killed.append)

    parked = reg.make_dormant(seat.registration_id, is_listening=lambda _p: True)
    assert parked is not None
    assert parked.chat_url == url
    assert _row(seat.registration_id)["status"] == "dormant"
    assert killed == [seat.port]
    assert not reg.process_holds_driver_lock(seat.registration_id)


def test_relaunch_pre_lock_not_dormant_raises_seat_contended(
    isolated_registry: Path,
) -> None:
    seat = _seat(chat_url="https://claude.ai/cowork/cse_pre_lock")
    reg.make_dormant(seat.registration_id, is_listening=lambda _p: False)
    active = reg._store.load_active()
    active[seat.registration_id]["status"] = "allocating"
    reg._store.write_active(active)
    with pytest.raises(reg.SeatContended) as excinfo:
        reg.relaunch_dormant(
            seat.registration_id,
            launch_chrome=_noop_launch,
            is_listening=lambda _p: False,
        )
    exc = excinfo.value
    assert exc.retryable is True
    assert exc.data["depth"] == "pre_lock_status_check"
    assert exc.data["registration_id"] == seat.registration_id
    assert exc.data["observed_status"] == "allocating"


def test_reserve_allocating_row_strips_seat_keys_from_carry(
    isolated_registry: Path,
) -> None:
    from claude_bundles.cdp_registry.lifecycle import reserve_allocating_row

    row, minted = reserve_allocating_row(
        holder="strip-test",
        purpose="operator-proxy",
        mission_kind="root",
        parent_thread="lane-1",
        listen=lambda _p: False,
        launch=False,
        carry={
            "seat_lane": "lane-1",
            "seat_bound_at": 99.0,
            "seat_closed_at": None,
        },
    )
    assert minted
    assert "seat_lane" not in row
    assert "seat_bound_at" not in row
    assert "seat_closed_at" not in row
