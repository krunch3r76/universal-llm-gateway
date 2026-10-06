"""Leg 1 — completed harvest reclaim. Real registry store, no write-through mock.

FORK A is the hook: terminal reached, row still active. FORK B (boot
reconcile) and FORK C (Stargate-only harvest) stay out of this file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from claude_bundles import cdp_registry as reg

from cdp_ask.execution_ladder import finish_execution
from cdp_ask.execution_store import ExecutionStore

pytestmark = pytest.mark.offline

CHAT = "https://claude.ai/cowork/cse_harvest"


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


def _seat(*, purpose: str = "ask", chat_url: str | None = CHAT) -> Any:
    seat = reg.register_lane(
        holder="harvest-test",
        purpose=purpose,
        launch_chrome=_noop_launch,
        is_listening=lambda _port: False,
    )
    if chat_url:
        reg.bind_session_address(seat.registration_id, chat_url=chat_url)
    reg._release_driver_lock(seat.registration_id)
    return seat


def _row(registration_id: str) -> dict[str, Any]:
    return reg._load_active()[registration_id]


async def _running(purpose: str, registration_id: str) -> tuple[ExecutionStore, str]:
    store = ExecutionStore(reaper_interval_s=9999.0)
    record = await store.create(holder="harvest-test", purpose=purpose)
    await store.set_registration_id(record.execution_id, registration_id)
    return store, record.execution_id


@pytest.mark.asyncio
async def test_content_proof_ladder_leaves_review_row_active(
    isolated_registry: Path,
) -> None:
    seat = _seat(purpose="review")
    store, execution_id = await _running("review", seat.registration_id)
    await store.update_ladder(execution_id, completion_phase="content_proof")
    row = _row(seat.registration_id)
    assert row["status"] == "active"
    assert row.get("chrome_pid") == 4242


@pytest.mark.asyncio
async def test_finish_execution_makes_ask_row_dormant_and_kills_listener(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seat = _seat(purpose="ask")
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", killed.append)
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _port: True)
    store, execution_id = await _running("ask", seat.registration_id)
    await finish_execution(store, execution_id, {"ok": True, "status": "completed"})
    row = _row(seat.registration_id)
    assert row["status"] == "dormant"
    assert row.get("dormant_reason") == "bus_terminal_harvest"
    assert killed == [seat.port]


@pytest.mark.asyncio
async def test_followup_in_flight_before_mark_terminal_leaves_row_untouched(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Snapshot is before mark_terminal: write-through replaces kind=followup."""
    from claude_bundles.cdp_registry.execution_state import set_execution_state

    seat = _seat(purpose="ask")
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", killed.append)
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _port: True)
    store, execution_id = await _running("ask", seat.registration_id)
    set_execution_state(
        seat.registration_id,
        execution_id="followup:harvest-test",
        state="streaming",
        kind="followup",
        reason="paste:test",
    )
    await finish_execution(store, execution_id, {"ok": True})
    row = _row(seat.registration_id)
    assert row["status"] == "active"
    assert row.get("chrome_pid") == 4242
    assert killed == []
    # mark_terminal did replace the followup entry; the hook must not have
    # re-read that settled state and parked the row.
    assert row["execution_state"]["kind"] == "execution"
    assert row["execution_state"]["state"] == "finished"


@pytest.mark.asyncio
async def test_later_execution_clears_followup_hold_and_second_finish_dormant(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Overlap paste on E1, finish E1 (stays active), seat E2, finish E2 → dormant.

    A hold must not outlive the execution that was in flight when the paste
    overlapped. Seating E2 is a later stamp and drops the hold, so harvest
    parks the row.
    """
    from claude_bundles.cdp_registry.execution_state import (
        FOLLOWUP_HOLD,
        set_execution_state,
    )

    seat = _seat(purpose="ask")
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", killed.append)
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _port: True)
    store, execution_id = await _running("ask", seat.registration_id)
    await store.update_liveness(
        execution_id,
        streaming=True,
        stop=False,
        tool_pause=False,
        liveness_observed_at=1.0,
    )
    set_execution_state(
        seat.registration_id,
        execution_id="followup:hold-f1",
        state="streaming",
        kind="followup",
        reason="paste:overlap",
    )
    assert FOLLOWUP_HOLD in _row(seat.registration_id)
    await finish_execution(store, execution_id, {"ok": True, "status": "completed"})
    after_e1 = _row(seat.registration_id)
    assert after_e1["status"] == "active"
    assert FOLLOWUP_HOLD in after_e1
    assert killed == []

    store_e2 = ExecutionStore(reaper_interval_s=9999.0)
    record_e2 = await store_e2.create(holder="harvest-test", purpose="ask")
    await store_e2.set_registration_id(record_e2.execution_id, seat.registration_id)
    assert FOLLOWUP_HOLD not in _row(seat.registration_id)
    await finish_execution(
        store_e2, record_e2.execution_id, {"ok": True, "status": "completed"}
    )
    assert _row(seat.registration_id)["status"] == "dormant"
    assert _row(seat.registration_id).get("dormant_reason") == "bus_terminal_harvest"


@pytest.mark.asyncio
async def test_purpose_outside_ask_review_leaves_row_untouched(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seat = _seat(purpose="fable")
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", killed.append)
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _port: True)
    store, execution_id = await _running("fable", seat.registration_id)
    await finish_execution(store, execution_id, {"ok": True})
    assert _row(seat.registration_id)["status"] == "active"
    assert killed == []


@pytest.mark.asyncio
async def test_finish_on_dormant_or_gone_row_emits_no_kill_decision(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from claude_bundles import cdp_registry_events as ev

    emitted: list[object] = []
    monkeypatch.setattr(ev, "emit", lambda event: emitted.append(event))

    seat = _seat(purpose="ask")
    reg.make_dormant(seat.registration_id, is_listening=lambda _port: False)
    store, execution_id = await _running("ask", seat.registration_id)
    emitted.clear()
    await finish_execution(store, execution_id, {"ok": True})
    assert _row(seat.registration_id)["status"] == "dormant"
    assert not any(
        getattr(event, "signal", None) == "cdp.port.exit_kill_decision"
        for event in emitted
    )

    gone = ExecutionStore(reaper_interval_s=9999.0)
    record = await gone.create(holder="harvest-test", purpose="ask")
    await gone.set_registration_id(record.execution_id, "reg-not-in-store")
    emitted.clear()
    await finish_execution(gone, record.execution_id, {"ok": True})
    assert not any(
        getattr(event, "signal", None) == "cdp.port.exit_kill_decision"
        for event in emitted
    )


@pytest.mark.asyncio
async def test_make_dormant_refusal_without_chat_url_is_logged(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seat = _seat(purpose="ask", chat_url=None)
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _port: True)
    store, execution_id = await _running("ask", seat.registration_id)
    await finish_execution(store, execution_id, {"ok": True})
    assert _row(seat.registration_id)["status"] == "active"
    log = (isolated_registry / "registry.jsonl").read_text(encoding="utf-8")
    assert "dormant_refused" in log
    assert "no_chat_url" in log
