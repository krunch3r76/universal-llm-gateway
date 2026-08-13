"""Tests for wake-debt lane retain (ep22 harvest gate)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_bundles.cdp_registry_store import load_sessions
from claude_bundles.cse_session_obligations import (
    append_session_transition_locked,
    fold_pending_transitions,
    stamp_session_ids,
)
from claude_bundles.cse_wake_retain import (
    discharge_superseded_seat_obligations,
    get_open_wake_owed_for_registration,
    registration_has_wake_debt,
    release_lane_if_debt_cleared,
    try_claim_wake_payment,
)
from claude_bundles.project_ask_abort import deregister_on_exit

pytestmark = pytest.mark.offline


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


def _seed_wake_debt(*, thread: str = "6661", reg: str = "reg-6661") -> None:
    stamp_session_ids(lane_thread=thread, registration_id=reg)
    append_session_transition_locked(
        {
            "event_id": f"protocol.parked:{thread}:1",
            "event": "cdp.protocol.parked",
            "ts": 1000.0,
            "payload": {
                "thread": thread,
                "turn_id": 1,
                "turn_number": 1,
                "parked_ts": 1000.0,
                "obligation_id": f"wake:{thread}:1",
                "wake_channel": "chat_delivery",
                "fallback": "bus_wake+pager",
                "cse_registration_id": reg,
                "skipped": False,
            },
        }
    )


def test_registration_has_wake_debt_after_parked(isolated_obligations: Path) -> None:
    _seed_wake_debt()
    assert registration_has_wake_debt("reg-6661") is True
    assert registration_has_wake_debt("reg-other") is False


def test_get_open_wake_owed_for_registration(isolated_obligations: Path) -> None:
    _seed_wake_debt()
    ob = get_open_wake_owed_for_registration(load_sessions(), "reg-6661")
    assert ob is not None
    assert ob["kind"] == "wake_owed"


def test_try_claim_wake_payment_idempotent(isolated_obligations: Path) -> None:
    _seed_wake_debt()
    assert try_claim_wake_payment(thread="6661", obligation_id="wake:6661:1") is True
    assert try_claim_wake_payment(thread="6661", obligation_id="wake:6661:1") is False
    sessions = load_sessions()
    ob = get_open_wake_owed_for_registration(sessions, "reg-6661")
    assert ob is not None
    assert (ob.get("payment") or {}).get("claimed") is True


def test_deregister_on_exit_skips_when_wake_debt(
    isolated_obligations: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_wake_debt()
    deregister = MagicMock()
    monkeypatch.setattr(
        "claude_bundles.project_ask_abort.cdp_registry.deregister_lane", deregister
    )
    monkeypatch.setattr(
        "claude_bundles.project_ask_abort.registration_owns_port", lambda *_a, **_k: True
    )
    reg = MagicMock(registration_id="reg-6661", port=9222, purpose="operator-proxy")
    deregister_on_exit(reg, purpose="operator-proxy")
    deregister.assert_not_called()


def test_release_lane_after_discharge(
    isolated_obligations: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_wake_debt()
    assert registration_has_wake_debt("reg-6661") is True
    from claude_bundles.cse_session_obligations import emit_wake_delivered_transition

    emit_wake_delivered_transition(
        registration_id="reg-6661",
        thread="6661",
        obligation_id="wake:6661:1",
        send_verified=True,
    )
    fold_pending_transitions()
    assert registration_has_wake_debt("reg-6661") is False

    deregister = MagicMock()
    list_active = MagicMock(
        return_value=[MagicMock(registration_id="reg-6661", purpose="operator-proxy")]
    )
    monkeypatch.setattr(
        "claude_bundles.cse_wake_retain.cdp_registry.list_active", list_active
    )
    monkeypatch.setattr(
        "claude_bundles.project_ask_abort.cdp_registry.deregister_lane", deregister
    )
    monkeypatch.setattr(
        "claude_bundles.project_ask_abort.registration_owns_port", lambda *_a, **_k: True
    )
    assert release_lane_if_debt_cleared("reg-6661", purpose="operator-proxy") is True
    deregister.assert_called_once()


def test_runner_wake_debt_extras(isolated_obligations: Path) -> None:
    _seed_wake_debt()
    from cdp_ask.runner import _wake_debt_extras

    assert _wake_debt_extras("reg-6661", ok=True) == {"awaiting_wake_debt": True}
    assert _wake_debt_extras("reg-other", ok=True) == {}


@pytest.mark.asyncio
async def test_execution_store_boot_reconcile_skips_wake_debt(
    isolated_obligations: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_wake_debt()
    from cdp_ask.execution_store import ExecutionStore

    store = ExecutionStore(reaper_interval_s=9999.0)
    deregister = MagicMock()
    import claude_bundles.cdp_registry as reg

    monkeypatch.setattr(
        reg,
        "_load_active",
        lambda: {
            "reg-6661": {
                "status": "active",
                "port": 9299,
                "profile_suffix": "reg-6661abcd",
            }
        },
    )
    monkeypatch.setattr(reg, "deregister_lane", deregister)
    import claude_bundles.cdp_orphans as orphans

    monkeypatch.setattr(orphans, "probe_live_ports", lambda port_range=None: [])
    import claude_bundles.cdp_lane as lane

    monkeypatch.setattr(lane, "is_listening", lambda _p: False)
    reaped = await store.boot_reconcile()
    assert reaped == []
    deregister.assert_not_called()


def _seed_stop_ack_debt(*, reg: str = "reg-stop") -> None:
    from claude_bundles.cse_session_obligations import mint_stop_ack_owed

    mint_stop_ack_owed(
        execution_id="exec-stop",
        registration_id=reg,
        purpose="operator-proxy",
        now=1000.0,
    )


def test_registration_has_wake_debt_includes_stop_ack_owed(
    isolated_obligations: Path,
) -> None:
    _seed_stop_ack_debt()
    assert registration_has_wake_debt("reg-stop") is True


def test_release_lane_blocked_by_open_stop_ack_owed(
    isolated_obligations: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_stop_ack_debt()
    list_active = MagicMock(
        return_value=[MagicMock(registration_id="reg-stop", purpose="operator-proxy")]
    )
    deregister = MagicMock()
    monkeypatch.setattr(
        "claude_bundles.cse_wake_retain.cdp_registry.list_active", list_active
    )
    monkeypatch.setattr(
        "claude_bundles.project_ask_abort.cdp_registry.deregister_lane", deregister
    )
    assert release_lane_if_debt_cleared("reg-stop", purpose="operator-proxy") is False
    deregister.assert_not_called()


def test_discharge_superseded_clears_wake_debt(
    isolated_obligations: Path,
) -> None:
    _seed_wake_debt()
    assert registration_has_wake_debt("reg-6661") is True
    assert discharge_superseded_seat_obligations(
        "reg-6661", successor_registration_id="reg-hop"
    ) == 1
    assert registration_has_wake_debt("reg-6661") is False
