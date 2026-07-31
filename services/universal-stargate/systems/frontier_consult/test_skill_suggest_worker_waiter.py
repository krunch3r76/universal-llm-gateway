"""Regression tests for event-driven skill-suggest worker completion."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from .skill_suggest_dispatch_closeout import (
    fetch_worker_closeout_body,
    map_wait_outcome_to_degraded_reason,
)
from .skill_suggest_dispatch_config import (
    SkillSuggestDispatchConfig,
    reset_skill_suggest_dispatch_config_cache,
)
from .skill_suggest_durable_state import (
    DurableTerminalEvent,
    LedgerDispatchRow,
    durable_catch_up_terminal,
    infer_terminal_from_ledger,
)
from .skill_suggest_worker_waiter import (
    WorkerWaitOutcome,
    await_worker_completion,
    reset_worker_completion_waiter_for_tests,
)


def _test_config(**overrides: float) -> SkillSuggestDispatchConfig:
    base = dict(
        idle_timeout_seconds=120.0,
        ack_window_seconds=3.0,
        idle_poll_interval_seconds=0.01,
        cortex_timeout_seconds=30.0,
        mcp_relay_timeout_seconds=330.0,
        agent_bus_wait_chunk_seconds=60.0,
        agent_bus_client_timeout_seconds=70.0,
        agent_bus_max_wait_seconds=60.0,
        worker_probe_timeout_seconds=10.0,
        worker_dispatch_http_timeout_seconds=10.0,
        wait_retry_backoff_seconds=1.0,
        worker_outer_timeout_seconds=1920.0,
    )
    base.update(overrides)
    return SkillSuggestDispatchConfig(**base)


@pytest.fixture(autouse=True)
def _reset_waiter_state() -> None:
    reset_worker_completion_waiter_for_tests()
    reset_skill_suggest_dispatch_config_cache()


@pytest.mark.offline
def test_config_loads_dispatch_section_from_pipeline_yaml() -> None:
    cfg = _test_config()
    loaded = __import__(
        "systems.frontier_consult.skill_suggest_dispatch_config",
        fromlist=["load_skill_suggest_dispatch_config"],
    ).load_skill_suggest_dispatch_config()
    assert loaded.idle_timeout_seconds == cfg.idle_timeout_seconds
    assert loaded.mcp_relay_timeout_seconds == 330.0


@pytest.mark.offline
def test_terminal_before_waiter_registration_via_catch_up() -> None:
    terminal = DurableTerminalEvent(
        signal="frontier.sdk.worker.completed",
        dispatch_id="d1",
        thread_id="2111",
        execution_id="exec-1",
        payload={"dispatch_id": "d1", "thread_id": "2111", "execution_id": "exec-1"},
    )
    with patch(
        "systems.frontier_consult.skill_suggest_durable_state.find_durable_terminal_event",
        return_value=terminal,
    ):
        got = durable_catch_up_terminal(
            execution_id="exec-1",
            thread_id="2111",
            dispatch_id="d1",
        )
    assert got is not None
    assert got.signal == "frontier.sdk.worker.completed"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_duplicate_terminal_completes_immediately() -> None:
    terminal = DurableTerminalEvent(
        signal="frontier.sdk.worker.completed",
        dispatch_id="d1",
        thread_id="2111",
        execution_id="exec-dup",
        payload={},
    )
    with patch(
        "systems.frontier_consult.skill_suggest_worker_waiter.durable_catch_up_terminal",
        return_value=terminal,
    ):
        outcome = await await_worker_completion(
            execution_id="exec-dup",
            dispatch_id="d1",
            thread_id="2111",
            config=_test_config(),
        )
    assert outcome.kind == "completed"


@pytest.mark.offline
def test_delivery_failed_maps_worker_no_reply() -> None:
    outcome = WorkerWaitOutcome(
        kind="delivery_failed",
        terminal=DurableTerminalEvent(
            signal="frontier.sdk.worker.delivery_failed",
            dispatch_id="d1",
            thread_id="2111",
            execution_id="exec-1",
            payload={},
        ),
    )
    reason = map_wait_outcome_to_degraded_reason(
        outcome,
        ledger=None,
        closeout_body=None,
        envelope_ok=False,
    )
    assert reason == "worker_no_reply"


@pytest.mark.offline
def test_ledger_failed_without_turn_is_worker_unreachable() -> None:
    outcome = WorkerWaitOutcome(kind="failed", terminal=None)
    ledger = LedgerDispatchRow(
        dispatch_id="d1",
        thread_id="2111",
        execution_id="exec-1",
        status="failed",
        terminal_status="failed",
        last_heartbeat_at=None,
        started_at=None,
        queued_at=None,
    )
    reason = map_wait_outcome_to_degraded_reason(
        outcome,
        ledger=ledger,
        closeout_body=None,
        envelope_ok=False,
    )
    assert reason == "worker_unreachable"


@pytest.mark.offline
def test_unparseable_envelope_is_worker_reply_unparseable() -> None:
    outcome = WorkerWaitOutcome(kind="completed", terminal=None)
    reason = map_wait_outcome_to_degraded_reason(
        outcome,
        ledger=None,
        closeout_body='{"status":"complete"}',
        envelope_ok=False,
    )
    assert reason == "worker_reply_unparseable"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_idle_budget_exceeded_yields_worker_idle_timeout() -> None:
    with (
        patch(
            "systems.frontier_consult.skill_suggest_worker_waiter.durable_catch_up_terminal",
            return_value=None,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_worker_waiter.read_ledger_dispatch_row",
            return_value=LedgerDispatchRow(
                dispatch_id="d1",
                thread_id="2111",
                execution_id="exec-idle",
                status="running",
                terminal_status=None,
                last_heartbeat_at="2000-01-01T00:00:00+00:00",
                started_at="2000-01-01T00:00:00+00:00",
                queued_at=None,
            ),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_worker_waiter.durable_idle_seconds",
            return_value=999.0,
        ),
    ):
        outcome = await await_worker_completion(
            execution_id="exec-idle",
            dispatch_id="d1",
            thread_id="2111",
            config=_test_config(idle_timeout_seconds=5.0),
        )
    assert outcome.kind == "idle_timeout"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_long_run_beyond_old_45s_succeeds_when_terminal_exists() -> None:
    terminal = DurableTerminalEvent(
        signal="frontier.sdk.worker.completed",
        dispatch_id="d-long",
        thread_id="2111",
        execution_id="exec-long",
        payload={},
    )

    call_count = 0

    def _catch_up(*_args: object, **_kwargs: object) -> DurableTerminalEvent | None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            return terminal
        return None

    with patch(
        "systems.frontier_consult.skill_suggest_worker_waiter.durable_catch_up_terminal",
        side_effect=_catch_up,
    ):
        outcome = await await_worker_completion(
            execution_id="exec-long",
            dispatch_id="d-long",
            thread_id="2111",
            config=_test_config(idle_timeout_seconds=120.0),
        )
    assert outcome.kind == "completed"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_completed_with_bus_post_lag_fetches_snapshot() -> None:
    class _Resp:
        status_code = 200

        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def json(self) -> dict:
            return self._payload

    class _Client:
        async def get(self, path: str, **_kwargs: object) -> _Resp:
            if path.endswith("/wait"):
                return _Resp(
                    {
                        "complete": True,
                        "qualifying_reply_turn": 2,
                    }
                )
            return _Resp({"body": json.dumps({"status": "complete"})})

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    with patch(
        "systems.frontier_consult.skill_suggest_dispatch_closeout.make_async_client",
        return_value=_Client(),
    ):
        body = await fetch_worker_closeout_body(
            thread_id="2111",
            headers={},
            config=_test_config(),
        )
    assert body is not None


@pytest.mark.offline
def test_infer_terminal_from_ledger_after_restart() -> None:
    ledger = LedgerDispatchRow(
        dispatch_id="d1",
        thread_id="2111",
        execution_id="exec-r",
        status="completed",
        terminal_status="completed",
        last_heartbeat_at="2026-06-23T12:00:00+00:00",
        started_at="2026-06-23T11:59:00+00:00",
        queued_at=None,
    )
    terminal = infer_terminal_from_ledger(ledger)
    assert terminal is not None
    assert terminal.signal == "frontier.sdk.worker.completed"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_idle_timeout_degraded_reason() -> None:
    from .skill_suggest_dispatch import (
        SkillSuggestDispatchRequest,
        dispatch_skill_suggest,
    )

    body = SkillSuggestDispatchRequest(agent="claude-cursor", loaded=[])
    fallback_payload = {
        "agent": "claude-cursor",
        "suggestions": [],
        "count": 0,
        "omitted": [],
        "degraded_skills": [],
        "loaded_echo": [],
        "seat_preloaded": [],
        "ranker_status": "pending",
        "degraded": True,
        "route": "fallback",
    }
    degraded_events: list[object] = []

    with (
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._fetch_extended_candidates",
            new_callable=AsyncMock,
            return_value=([], [], []),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.dispatch_cursor_sdk_generate",
            new_callable=AsyncMock,
            return_value={
                "execution_id": "exec-idle-d",
                "thread_id": "2111",
                "dispatch_id": "d-idle",
            },
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_ack",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_completion",
            new_callable=AsyncMock,
            return_value=WorkerWaitOutcome(kind="idle_timeout"),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.fetch_worker_closeout_body",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.run_fallback",
            new_callable=AsyncMock,
            return_value=fallback_payload,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._publish_event",
            side_effect=lambda event: degraded_events.append(event),
        ),
    ):
        result = await dispatch_skill_suggest(request_id="req-idle", body=body)

    assert result["degraded_reason"] == "worker_idle_timeout"
    assert degraded_events
    assert degraded_events[0].payload["execution_id"] == "exec-idle-d"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_ledger_absent_probe_fail_is_worker_unreachable() -> None:
    from .skill_suggest_dispatch import (
        SkillSuggestDispatchRequest,
        dispatch_skill_suggest,
    )

    body = SkillSuggestDispatchRequest(agent="claude-cursor", loaded=[])
    fallback_payload = {
        "agent": "claude-cursor",
        "suggestions": [],
        "count": 0,
        "omitted": [],
        "degraded_skills": [],
        "loaded_echo": [],
        "seat_preloaded": [],
        "ranker_status": "pending",
        "degraded": True,
        "degraded_reason": "worker_unreachable",
        "route": "fallback",
    }

    with (
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._fetch_extended_candidates",
            new_callable=AsyncMock,
            return_value=([], [], []),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.dispatch_cursor_sdk_generate",
            new_callable=AsyncMock,
            return_value={
                "execution_id": "exec-u",
                "thread_id": "2111",
                "dispatch_id": "d-u",
            },
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_ack",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.run_fallback",
            new_callable=AsyncMock,
            return_value=fallback_payload,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._publish_event",
        ),
    ):
        result = await dispatch_skill_suggest(request_id="req-unreach", body=body)

    assert result["degraded_reason"] == "worker_unreachable"
