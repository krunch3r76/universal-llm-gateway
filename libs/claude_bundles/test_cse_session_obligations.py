"""Tests for CSR obligations plane — AC1–AC5, AC-R1, AC-B2."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_bundles import cdp_registry_events as events
from claude_bundles.cdp_registry_store import load_sessions
from claude_bundles.cse_session_obligations import (
    WAKE_TTL_S,
    append_session_transition_locked,
    emit_wake_delivered_transition,
    fold_pending_transitions,
    get_open_wake_owed,
    maybe_mirror_protocol_turn,
    stamp_session_ids,
    sweep_wake_owed_ttl,
)

pytestmark = pytest.mark.offline

EP15_DIAGNOSE_TS = 1754272206.0  # 2026-08-04 01:30:06Z


@pytest.fixture
def isolated_obligations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    import claude_bundles.cdp_registry_store as store

    monkeypatch.setattr(store, "REGISTRY_DIR", root)
    monkeypatch.setattr(store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(store, "SESSIONS_JSON", root / "sessions.json")
    monkeypatch.setattr(
        store, "SESSION_TRANSITIONS_JSONL", root / "session_transitions.jsonl"
    )
    monkeypatch.setattr(store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(store, "REGISTRATIONS_DIR", regs)
    return root


def _parked_body(**fields: str) -> str:
    lines = ["TYPE: PARKED"]
    for key, val in fields.items():
        lines.append(f"{key}: {val}")
    return "\n".join(lines)


def test_stamp_session_ids_prefers_url_derived_cse_id(
    isolated_obligations: Path,
) -> None:
    """Arc 6885: cse_id is session address, never registration_id."""
    url = "https://claude.ai/cowork/cse_01StampTestId0001"
    stamp_session_ids(
        lane_thread="6885",
        chat_url=url,
        registration_id="reg-not-a-session",
    )
    sessions = load_sessions()
    found = next(iter(sessions.values()))
    assert found["cse_id"] == "cse_01StampTestId0001"
    assert found["ids"]["registration_id"] == "reg-not-a-session"
    assert found["ids"]["chat_url"] == url


def test_stamp_session_ids_pending_without_url(isolated_obligations: Path) -> None:
    stamp_session_ids(lane_thread="6885", registration_id="reg-only")
    sessions = load_sessions()
    found = next(iter(sessions.values()))
    assert found["cse_id"] == "pending-6885"
    assert found["cse_id"] != "reg-only"


def test_ac1_parked_mirror_survives_restart(isolated_obligations: Path) -> None:
    """AC1: wake_owed readable from disk after reload."""
    stamp_session_ids(
        lane_thread="6655",
        chat_url="https://claude.ai/chat/ep15",
        registration_id="reg-6655",
    )
    body = _parked_body(
        wake="chat_delivery",
        fallback="bus_wake+pager",
        cse_chat_url="https://claude.ai/chat/ep15",
        cse_registration_id="reg-6655",
    )
    maybe_mirror_protocol_turn(
        thread="6655",
        turn_id=1177,
        turn_number=1177,
        created_at="1754265000.0",
        body=body,
    )
    assert (isolated_obligations / "sessions.json").exists()
    on_disk = json.loads((isolated_obligations / "sessions.json").read_text(encoding="utf-8"))
    assert any(
        ob.get("kind") == "wake_owed" and ob.get("status") == "open"
        for row in on_disk.values()
        for ob in row.get("obligations") or []
    )
    reloaded = load_sessions()
    ob = get_open_wake_owed(reloaded, thread="6655")
    assert ob is not None
    assert ob["kind"] == "wake_owed"


def test_ac2_ep15_fixture_alarm_before_diagnose(isolated_obligations: Path) -> None:
    """AC2: offline fold yields alarm before human DIAGNOSE timestamp."""
    parked_ts = EP15_DIAGNOSE_TS - WAKE_TTL_S - 60.0
    stamp_session_ids(lane_thread="6655", registration_id="reg-6655")
    append_session_transition_locked(
        {
            "event_id": "protocol.parked:6655:1177",
            "event": "cdp.protocol.parked",
            "ts": parked_ts,
            "payload": {
                "thread": "6655",
                "turn_id": 1177,
                "turn_number": 1177,
                "parked_ts": parked_ts,
                "obligation_id": "wake:6655:1177",
                "wake_channel": "chat_delivery",
                "fallback": "bus_wake+pager",
                "cse_registration_id": "reg-6655",
                "skipped": False,
            },
        }
    )
    alarms = sweep_wake_owed_ttl(
        now=EP15_DIAGNOSE_TS - 30.0, notify_pager=lambda _s, _b: True
    )
    assert alarms
    sessions = load_sessions()
    ob = get_open_wake_owed(sessions, thread="6655")
    assert ob is not None
    assert ob["status"] == "alarmed"
    assert float(ob["alarm"]["fired_at"]) < EP15_DIAGNOSE_TS




def test_ac4_wake_delivered_idempotent(isolated_obligations: Path) -> None:
    """AC4: duplicate receipt is no-op."""
    stamp_session_ids(lane_thread="6655", registration_id="reg-6655")
    append_session_transition_locked(
        {
            "event_id": "protocol.parked:6655:1",
            "event": "cdp.protocol.parked",
            "ts": 1000.0,
            "payload": {
                "thread": "6655",
                "turn_id": 1,
                "parked_ts": 1000.0,
                "obligation_id": "wake:6655:1",
                "cse_registration_id": "reg-6655",
                "skipped": False,
            },
        }
    )
    emit_wake_delivered_transition(
        registration_id="reg-6655",
        thread="6655",
        obligation_id="wake:6655:1",
        send_verified=True,
    )
    assert get_open_wake_owed(load_sessions(), thread="6655") is None
    emit_wake_delivered_transition(
        registration_id="reg-6655",
        thread="6655",
        obligation_id="wake:6655:1",
        send_verified=True,
    )
    discharged = [
        o
        for row in load_sessions().values()
        for o in row.get("obligations") or []
        if o.get("obligation_id") == "wake:6655:1"
    ]
    assert len(discharged) == 1
    assert discharged[0]["status"] == "discharged"


def test_ac4_emit_site_only_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4/B2: transition emit fires on followup success path."""
    emitted: list[str] = []

    def _capture(evt: events.Event) -> None:
        emitted.append(evt.signal)

    monkeypatch.setattr(events, "_mirror_to_event_service", _capture)
    emit_wake_delivered_transition(
        registration_id="reg-x",
        thread="1",
        obligation_id="wake:1:1",
        send_verified=True,
    )
    assert emitted == ["cdp.wake.delivered"]


def test_ac5_silent_debt_unconstructible(isolated_obligations: Path) -> None:
    """AC5: empty projection when no transitions folded."""
    assert load_sessions() == {}
    fold_pending_transitions()
    assert load_sessions() == {}


def test_ac_r1_kill_retain_not_gated_on_obligation() -> None:
    """AC-R1: reaper does not gate on wake_owed."""
    reaper = Path(__file__).resolve().parent / "cdp_lane_reaper.py"
    text = reaper.read_text(encoding="utf-8")
    assert "wake_owed" not in text


def test_ac_b2_no_seat_prose_discharge_path(isolated_obligations: Path) -> None:
    """AC-B2: in-memory status mutation does not persist without transition."""
    stamp_session_ids(lane_thread="6655", registration_id="reg-6655")
    append_session_transition_locked(
        {
            "event_id": "protocol.parked:6655:2",
            "event": "cdp.protocol.parked",
            "ts": 1000.0,
            "payload": {
                "thread": "6655",
                "turn_id": 2,
                "parked_ts": 1000.0,
                "obligation_id": "wake:6655:2",
                "cse_registration_id": "reg-6655",
                "skipped": False,
            },
        }
    )
    sessions = load_sessions()
    ob = get_open_wake_owed(sessions, thread="6655")
    assert ob is not None
    ob["status"] = "discharged"
    assert get_open_wake_owed(load_sessions(), thread="6655") is not None


def test_stop_ack_owed_mint_and_discharge(isolated_obligations: Path) -> None:
    from claude_bundles.cse_session_obligations import (
        discharge_stop_ack_owed,
        get_open_stop_ack_owed_for_execution,
        mint_stop_ack_owed,
    )

    mint_stop_ack_owed(
        execution_id="exec-sa",
        registration_id="reg-sa",
        purpose="mission",
        now=2000.0,
    )
    fold_pending_transitions()
    ob = get_open_stop_ack_owed_for_execution("exec-sa")
    assert ob is not None
    assert ob["kind"] == "stop_ack_owed"
    assert ob["status"] == "open"

    discharge_stop_ack_owed(execution_id="exec-sa", reason="intentional")
    fold_pending_transitions()
    ob = get_open_stop_ack_owed_for_execution("exec-sa")
    assert ob is None


def test_stop_ack_owed_ttl_alarm_persists(isolated_obligations: Path) -> None:
    from claude_bundles.cse_session_obligations import (
        get_open_stop_ack_owed_for_execution,
        mint_stop_ack_owed,
        sweep_stop_ack_owed_ttl,
    )

    mint_stop_ack_owed(
        execution_id="exec-ttl",
        registration_id="reg-ttl",
        purpose="mission",
        now=100.0,
    )
    sweep_stop_ack_owed_ttl(now=500.0, notify_pager=lambda _s, _b: True)
    fold_pending_transitions()
    ob = get_open_stop_ack_owed_for_execution("exec-ttl")
    assert ob is not None
    assert ob["status"] == "alarmed"
    assert ob["alarm"]["ghost_reap_candidate"] is True
