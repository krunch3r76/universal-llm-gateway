"""Unit tests for STOP-ACK check-in timer and ACK parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cdp_ask.execution_store import ExecutionRecord, ExecutionStore
from cdp_ask.models import FollowupProjectAskResponse
from cdp_ask.stop_ack_checkin import (
    STOP_ACK_QUIET_S,
    is_stop_ack_candidate,
    parse_stop_ack,
    run_checkin_tick,
)
from claude_bundles.cse_session_obligations import (
    get_open_stop_ack_owed_for_execution,
    mint_stop_ack_owed,
    sweep_stop_ack_owed_ttl,
)
from claude_bundles.cse_session_fold import fold_pending_transitions

pytestmark = pytest.mark.offline


def _candidate_record(
    *,
    execution_id: str = "exec-1",
    purpose: str = "operator-proxy",
    completion_phase: str = "running",
    streaming: bool | None = False,
    stop: bool | None = None,
    liveness_observed_at: float = 100.0,
) -> ExecutionRecord:
    return ExecutionRecord(
        execution_id=execution_id,
        status="running",
        created_at=90.0,
        updated_at=100.0,
        registration_id="reg-1",
        holder="seat",
        purpose=purpose,
        completion_phase=completion_phase,  # type: ignore[arg-type]
        streaming=streaming,
        stop=stop,
        liveness_observed_at=liveness_observed_at,
    )


def test_is_stop_ack_candidate_mission_stream_stopped() -> None:
    rec = _candidate_record()
    assert is_stop_ack_candidate(rec, now=100.0 + STOP_ACK_QUIET_S + 1) is True


def test_is_stop_ack_candidate_excludes_ask_purpose() -> None:
    rec = _candidate_record(purpose="ask")
    assert is_stop_ack_candidate(rec, now=1000.0) is False


def test_is_stop_ack_candidate_excludes_awaiting_wake() -> None:
    rec = _candidate_record(completion_phase="awaiting_wake")
    assert is_stop_ack_candidate(rec, now=1000.0) is False


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("STOP-ACK intentional", "intentional"),
        ("STOP-ACK unintentional", "unintentional"),
        ("STOP-ACK parked job-42", "parked"),
        ("Thanks for checking in", None),
        ("", None),
    ],
)
def test_parse_stop_ack_tokens(body: str, expected: str | None) -> None:
    parsed = parse_stop_ack(body)
    if expected is None:
        assert parsed is None
    else:
        assert parsed is not None
        assert parsed.route.value == expected
        if expected == "parked":
            assert parsed.job == "job-42"


def test_parse_stop_ack_parked_job() -> None:
    parsed = parse_stop_ack("line\nSTOP-ACK parked my-job\n")
    assert parsed is not None
    assert parsed.route.value == "parked"
    assert parsed.job == "my-job"


@pytest.mark.asyncio
async def test_run_checkin_unresolvable_identity_bus_wake_pager(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Unresolvable identity => lane_created=false and bus_wake+pager route."""
    import claude_bundles.cdp_registry_store as store

    root = tmp_path / "cdp-registry"
    root.mkdir()
    (root / "registrations").mkdir()
    monkeypatch.setattr(store, "REGISTRY_DIR", root)
    monkeypatch.setattr(store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(store, "SESSIONS_JSON", root / "sessions.json")
    monkeypatch.setattr(
        store, "SESSION_TRANSITIONS_JSONL", root / "session_transitions.jsonl"
    )
    monkeypatch.setattr(store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(store, "REGISTRATIONS_DIR", root / "registrations")

    store_obj = ExecutionStore()
    rec = _candidate_record()
    store_obj._records[rec.execution_id] = rec  # noqa: SLF001

    pager_calls: list[tuple[str, str]] = []
    emitted: list[str] = []

    async def _fail_resolve(req, _store):
        from cdp_ask.followup_resolve import fail_followup

        return None, fail_followup("cse_not_found_on_lane"), "execution_id"

    monkeypatch.setattr(
        "cdp_ask.followup_resolve.resolve_followup_target", _fail_resolve
    )

    with patch("cdp_ask.stop_ack_checkin.emit_stop_ack_event") as emit_mock:
        emit_mock.side_effect = lambda evt: emitted.append(evt.signal)
        results = await run_checkin_tick(
            store_obj,
            now=1000.0,
            notify_pager=lambda s, b: pager_calls.append((s, b)) or True,
        )

    assert pager_calls
    assert any(r.get("route") == "bus_wake+pager" for r in results)
    assert "cdp_ask.stop_ack.checkin_attempt" in emitted


@pytest.mark.asyncio
async def test_run_checkin_parsed_ack_discharges(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import claude_bundles.cdp_registry_store as store

    root = tmp_path / "cdp-registry"
    root.mkdir()
    (root / "registrations").mkdir()
    monkeypatch.setattr(store, "REGISTRY_DIR", root)
    monkeypatch.setattr(store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(store, "SESSIONS_JSON", root / "sessions.json")
    monkeypatch.setattr(
        store, "SESSION_TRANSITIONS_JSONL", root / "session_transitions.jsonl"
    )
    monkeypatch.setattr(store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(store, "REGISTRATIONS_DIR", root / "registrations")

    store_obj = ExecutionStore()
    rec = _candidate_record()
    store_obj._records[rec.execution_id] = rec  # noqa: SLF001

    target = MagicMock(registration_id="reg-1", chat_url="https://claude.ai/chat/x")

    async def _resolve(req, _store):
        return target, None, "registration_id"

    async def _followup(req, _store):
        return FollowupProjectAskResponse(
            ok=True,
            registration_id="reg-1",
            send_verified=True,
            receipt="dom_paste",
            lane_created=False,
        )

    monkeypatch.setattr(
        "cdp_ask.followup_resolve.resolve_followup_target", _resolve
    )
    monkeypatch.setattr(
        "cdp_ask.stop_ack_checkin._harvest_reply_body",
        AsyncMock(return_value="STOP-ACK intentional"),
    )
    emitted: list[str] = []
    with patch("cdp_ask.stop_ack_checkin.emit_stop_ack_event") as emit_mock:
        emit_mock.side_effect = lambda evt: emitted.append(evt.signal)
        await run_checkin_tick(
            store_obj,
            now=1000.0,
            execute_followup_fn=_followup,
        )

    fold_pending_transitions()
    assert get_open_stop_ack_owed_for_execution("exec-1") is None
    assert "cdp_ask.stop_ack.ack" in emitted


def test_sweep_stop_ack_owed_ttl_alarms(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import claude_bundles.cdp_registry_store as store

    root = tmp_path / "cdp-registry"
    root.mkdir()
    (root / "registrations").mkdir()
    monkeypatch.setattr(store, "REGISTRY_DIR", root)
    monkeypatch.setattr(store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(store, "SESSIONS_JSON", root / "sessions.json")
    monkeypatch.setattr(
        store, "SESSION_TRANSITIONS_JSONL", root / "session_transitions.jsonl"
    )
    monkeypatch.setattr(store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(store, "REGISTRATIONS_DIR", root / "registrations")

    mint_stop_ack_owed(
        execution_id="exec-alarm",
        registration_id="reg-alarm",
        purpose="mission",
        now=100.0,
    )
    pager: list[str] = []
    alarms = sweep_stop_ack_owed_ttl(
        now=100.0 + 400.0,
        notify_pager=lambda s, _b: pager.append(s) or True,
    )
    assert alarms
    fold_pending_transitions()
    ob = get_open_stop_ack_owed_for_execution("exec-alarm")
    assert ob is not None
    assert ob["status"] == "alarmed"
    assert ob["alarm"]["ghost_reap_candidate"] is True
    assert pager


def test_stop_ack_events_emit_mocked() -> None:
    from cdp_ask.stop_ack_events import (
        cdp_ask_stop_ack_ack,
        cdp_ask_stop_ack_checkin_attempt,
        cdp_ask_stop_ack_no_ack,
        emit,
    )

    with patch("cdp_ask.stop_ack_events.socket.socket") as sock_cls:
        sock = MagicMock()
        sock_cls.return_value.__enter__.return_value = sock
        emit(
            cdp_ask_stop_ack_checkin_attempt(
                execution_id="e1",
                purpose="mission",
                route="paste",
                lane_created=False,
            )
        )
        emit(
            cdp_ask_stop_ack_ack(
                execution_id="e1",
                ack="intentional",
            )
        )
        emit(
            cdp_ask_stop_ack_no_ack(
                execution_id="e1",
                registration_id="reg-1",
            )
        )
        assert sock.sendall.call_count == 3
