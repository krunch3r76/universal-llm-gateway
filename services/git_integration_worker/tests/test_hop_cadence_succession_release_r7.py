"""R7 — terminalize superseded execution on succession confirm (arc 7119)."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from claude_bundles.project_ask_abort import AbortCleanupOutcome
from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
    PRIOR_NONE_EXECUTION,
    PRIOR_NONE_REGISTRATION,
    PredecessorHandle,
    PredecessorVerdict,
)
from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
    MAX_RELEASE_OBLIGATION_RETRIES,
    reconcile_succession_confirmations,
)
from services.git_integration_worker.cursor_auto.hop_cadence_succession_release import (
    RELEASE_IDLE_STREAK_REQUIRED,
    predecessor_in_flight,
    predecessor_mid_turn,
    release_superseded_on_confirm,
)

pytestmark = pytest.mark.offline

_INCUMBENT_EXEC = "exec-incumbent-r7"
_SUCCESSOR_EXEC = "exec-successor-r7"
_OTHER_A = "exec-other-a"
_OTHER_B = "exec-other-b"

_ALL_ABORT_OUTCOMES: tuple[AbortCleanupOutcome, ...] = (
    "attested_stopped_and_deregistered",
    "still_attached",
    "probe_inconclusive",
    "stop_transport_failed",
    "stopped_deregister_failed",
    "detached_remote_running",
    "ownership_lost",
    "lane_inactive",
    "already_done",
    "no_registration",
)


def _handle(*, verdict: PredecessorVerdict = PredecessorVerdict.INCUMBENT_RECORDED) -> PredecessorHandle:
    if verdict == PredecessorVerdict.FIRST_SEAT_ON_LANE:
        return PredecessorHandle(
            registration_id=PRIOR_NONE_REGISTRATION,
            execution_id=PRIOR_NONE_EXECUTION,
            verdict=verdict,
            absence_reason="no_registration_id_on_watch_at_hop_fire",
        )
    if verdict == PredecessorVerdict.LOOKUP_FAILED:
        return PredecessorHandle(
            registration_id="reg-stale",
            execution_id="",
            verdict=verdict,
        )
    return PredecessorHandle(
        registration_id="reg-old",
        execution_id=_INCUMBENT_EXEC,
        verdict=verdict,
    )


def _poll(
    *,
    status: str = "running",
    streaming: bool = False,
    stop: bool = False,
    tool_pause: bool = False,
) -> dict[str, Any]:
    return {
        "execution_id": _INCUMBENT_EXEC,
        "status": status,
        "streaming": streaming,
        "stop": stop,
        "tool_pause": tool_pause,
    }


def _client(
    *,
    poll: dict[str, Any] | None = None,
    abort: dict[str, Any] | None = None,
) -> MagicMock:
    client = MagicMock()
    client.poll.return_value = poll or _poll()
    client.abort.return_value = abort or {
        "execution_id": _INCUMBENT_EXEC,
        "status": "aborted",
        "aborted": True,
        "abort_outcome": "attested_stopped_and_deregistered",
    }
    return client


def _idle_streak_for_abort() -> int:
    return RELEASE_IDLE_STREAK_REQUIRED - 1


@pytest.mark.parametrize(
    ("bit", "reason"),
    [
        ("streaming", "predecessor_streaming"),
        ("stop", "predecessor_stop"),
        ("tool_pause", "predecessor_tool_pause"),
    ],
)
def test_predecessor_in_flight_each_bit(bit: str, reason: str) -> None:
    poll = _poll(**{bit: True})
    mid, got_reason = predecessor_in_flight(poll)
    assert mid is True
    assert got_reason == reason
    mid_alias, alias_reason = predecessor_mid_turn(poll)
    assert mid_alias is True
    assert alias_reason == reason


def test_predecessor_in_flight_idle_running() -> None:
    mid, reason = predecessor_in_flight(_poll())
    assert mid is False
    assert reason is None


def test_release_skipped_for_first_seat_verdict() -> None:
    result = release_superseded_on_confirm(_handle(verdict=PredecessorVerdict.FIRST_SEAT_ON_LANE))
    assert result["action"] == "skipped"
    assert result["reason"] == "verdict_first_seat_on_lane"


def test_release_skipped_for_lookup_failed_verdict() -> None:
    result = release_superseded_on_confirm(_handle(verdict=PredecessorVerdict.LOOKUP_FAILED))
    assert result["action"] == "skipped"
    assert result["reason"] == "verdict_lookup_failed"


def test_release_terminalizes_only_when_aborted_true() -> None:
    client = _client()
    result = release_superseded_on_confirm(
        _handle(),
        client=client,
        idle_streak=_idle_streak_for_abort(),
    )
    assert result["action"] == "terminalized"
    assert result["execution_id"] == _INCUMBENT_EXEC
    assert result["abort_outcome"] == "attested_stopped_and_deregistered"
    client.poll.assert_called_once_with(_INCUMBENT_EXEC)
    client.abort.assert_called_once_with(_INCUMBENT_EXEC)


@pytest.mark.parametrize("abort_outcome", _ALL_ABORT_OUTCOMES)
def test_release_non_true_abort_outcome_records_error(abort_outcome: str) -> None:
    aborted = abort_outcome == "attested_stopped_and_deregistered"
    client = _client(
        abort={
            "execution_id": _INCUMBENT_EXEC,
            "status": "aborted" if aborted else "running",
            "aborted": aborted,
            "abort_outcome": abort_outcome,
        }
    )
    result = release_superseded_on_confirm(
        _handle(),
        client=client,
        idle_streak=_idle_streak_for_abort(),
    )
    if aborted:
        assert result["action"] == "terminalized"
    else:
        assert result["action"] == "error"
        assert result["abort_outcome"] == abort_outcome
        assert "terminalized" not in result["action"]


def test_release_unrecognised_abort_outcome_fails_toward_error() -> None:
    client = _client(
        abort={
            "execution_id": _INCUMBENT_EXEC,
            "status": "running",
            "aborted": False,
            "abort_outcome": "unknown_future_value",
        }
    )
    result = release_superseded_on_confirm(
        _handle(),
        client=client,
        idle_streak=_idle_streak_for_abort(),
    )
    assert result["action"] == "error"
    assert result["abort_outcome"] == "unknown_future_value"


@pytest.mark.parametrize(
    ("bit", "reason"),
    [
        ("streaming", "predecessor_streaming"),
        ("stop", "predecessor_stop"),
        ("tool_pause", "predecessor_tool_pause"),
    ],
)
def test_release_deferred_mid_turn_each_bit(bit: str, reason: str) -> None:
    client = _client(poll=_poll(**{bit: True}))
    result = release_superseded_on_confirm(
        _handle(),
        client=client,
        idle_streak=_idle_streak_for_abort(),
    )
    assert result["action"] == "deferred"
    assert result["reason"] == reason
    assert result["idle_streak"] == 0
    client.abort.assert_not_called()


def test_release_deferred_when_idle_streak_not_yet_satisfied() -> None:
    client = _client()
    result = release_superseded_on_confirm(_handle(), client=client, idle_streak=0)
    assert result["action"] == "deferred"
    assert result["reason"] == "predecessor_idle_streak_unsatisfied"
    assert result["idle_streak"] == 1
    assert result["idle_streak_required"] == RELEASE_IDLE_STREAK_REQUIRED
    client.abort.assert_not_called()


def test_release_already_terminal_skips_abort() -> None:
    client = _client(poll=_poll(status="completed"))
    result = release_superseded_on_confirm(_handle(), client=client)
    assert result["action"] == "already_terminal"
    assert result["status"] == "completed"
    client.abort.assert_not_called()


def _snap_three_operator_proxy(*, admission_count: int = 3) -> dict[str, Any]:
    return {
        "admission_count": admission_count,
        "rows": [
            {
                "execution_id": _SUCCESSOR_EXEC,
                "registration_id": "reg-new",
                "status": "running",
                "purpose": "operator-proxy",
            },
            {
                "execution_id": _INCUMBENT_EXEC,
                "registration_id": "reg-old",
                "status": "running",
                "purpose": "operator-proxy",
            },
            {
                "execution_id": _OTHER_A,
                "registration_id": "reg-a",
                "status": "running",
                "purpose": "operator-proxy",
            },
            {
                "execution_id": _OTHER_B,
                "registration_id": "reg-b",
                "status": "running",
                "purpose": "operator-proxy",
            },
        ],
    }


def _watch_incumbent(*, registration_id: str = "reg-new") -> dict[str, Any]:
    return {
        "thread_id": "6885",
        "registration_id": registration_id,
        "successor_execution_id": "stargate-uuid",
        "pending_satellite_execution_id": _SUCCESSOR_EXEC,
        "superseded_registration_id": "reg-old",
        "superseded_execution_id": _INCUMBENT_EXEC,
        "predecessor_verdict": PredecessorVerdict.INCUMBENT_RECORDED.value,
    }


def _reconcile_patches(watches: dict[str, Any]):
    return (
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.load_watches",
            side_effect=lambda path=None: watches,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.save_watches",
            side_effect=lambda data, path=None: watches.update(data) or None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_succession_confirmed",
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_registration_advanced",
        ),
    )


def _enter_reconcile_patches(watches: dict[str, Any]) -> ExitStack:
    stack = ExitStack()
    for patcher in _reconcile_patches(watches):
        stack.enter_context(patcher)
    return stack


def test_reconcile_releases_only_incumbent_recorded() -> None:
    snap = _snap_three_operator_proxy()
    watches = {"6885": _watch_incumbent(registration_id="reg-old")}
    terminalized: list[str] = []

    def _release(handle: PredecessorHandle, idle_streak: int = 0) -> dict[str, Any]:
        if handle.verdict == PredecessorVerdict.INCUMBENT_RECORDED:
            if idle_streak < _idle_streak_for_abort():
                return {
                    "action": "deferred",
                    "execution_id": handle.execution_id,
                    "reason": "predecessor_idle_streak_unsatisfied",
                    "idle_streak": idle_streak + 1,
                }
            terminalized.append(handle.execution_id)
            snap["admission_count"] -= 1
            snap["rows"] = [r for r in snap["rows"] if r["execution_id"] != handle.execution_id]
            return {"action": "terminalized", "execution_id": handle.execution_id}
        return {"action": "skipped", "reason": f"verdict_{handle.verdict.value}"}

    with _enter_reconcile_patches(watches):
        result = reconcile_succession_confirmations(
            snapshot_reader=lambda: snap,
            release_fn=_release,
        )
        assert len(result["confirmations"]) == 1
        assert result["releases"][0]["action"] == "deferred"
        assert watches["6885"]["release_obligation"]["status"] == "pending"

        result2 = reconcile_succession_confirmations(
            snapshot_reader=lambda: snap,
            release_fn=_release,
        )
        assert len(result2["obligation_retries"]) == 1
        assert result2["obligation_retries"][0]["action"] == "deferred"

        result3 = reconcile_succession_confirmations(
            snapshot_reader=lambda: snap,
            release_fn=_release,
        )
        assert len(result3["obligation_retries"]) == 1
        assert result3["obligation_retries"][0]["action"] == "terminalized"
        assert terminalized == [_INCUMBENT_EXEC]
        assert snap["admission_count"] == 2
        assert "release_obligation" not in watches["6885"]


def test_reconcile_error_persists_obligation_and_retries_to_terminalized() -> None:
    snap = _snap_three_operator_proxy()
    watches = {"6885": _watch_incumbent(registration_id="reg-old")}
    attempts = {"count": 0}

    def _release(handle: PredecessorHandle, idle_streak: int = 0) -> dict[str, Any]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return {
                "action": "error",
                "execution_id": handle.execution_id,
                "abort_outcome": "still_attached",
            }
        if idle_streak < _idle_streak_for_abort():
            return {
                "action": "deferred",
                "execution_id": handle.execution_id,
                "reason": "predecessor_idle_streak_unsatisfied",
                "idle_streak": idle_streak + 1,
            }
        snap["admission_count"] -= 1
        return {"action": "terminalized", "execution_id": handle.execution_id}

    with _enter_reconcile_patches(watches):
        first = reconcile_succession_confirmations(snapshot_reader=lambda: snap, release_fn=_release)
        assert first["releases"][0]["action"] == "error"
        assert watches["6885"]["release_obligation"]["status"] == "pending"
        assert len(first["confirmations"]) == 1

        second = reconcile_succession_confirmations(snapshot_reader=lambda: snap, release_fn=_release)
        assert second["obligation_retries"][0]["action"] == "deferred"

        third = reconcile_succession_confirmations(snapshot_reader=lambda: snap, release_fn=_release)
        assert third["obligation_retries"][0]["action"] == "deferred"

        fourth = reconcile_succession_confirmations(snapshot_reader=lambda: snap, release_fn=_release)
        assert fourth["obligation_retries"][0]["action"] == "terminalized"
        assert snap["admission_count"] == 2


def test_obligation_reaches_failed_after_max_retries() -> None:
    row = {
        "thread_id": "6885",
        "superseded_execution_id": _INCUMBENT_EXEC,
        "superseded_registration_id": "reg-old",
        "predecessor_verdict": PredecessorVerdict.INCUMBENT_RECORDED.value,
        "release_obligation": {
            "execution_id": _INCUMBENT_EXEC,
            "registration_id": "reg-old",
            "verdict": PredecessorVerdict.INCUMBENT_RECORDED.value,
            "status": "pending",
            "retry_count": MAX_RELEASE_OBLIGATION_RETRIES - 1,
            "idle_streak": _idle_streak_for_abort(),
        },
    }
    watches = {"6885": row}

    def _release(handle: PredecessorHandle, idle_streak: int = 0) -> dict[str, Any]:
        return {
            "action": "error",
            "execution_id": handle.execution_id,
            "abort_outcome": "still_attached",
        }

    with _enter_reconcile_patches(watches):
        result = reconcile_succession_confirmations(snapshot_reader=lambda: _snap_three_operator_proxy(), release_fn=_release)

    assert result["obligation_retries"][0]["action"] == "error"
    assert watches["6885"]["release_obligation"]["status"] == "failed"
    assert watches["6885"]["release_obligation"]["failure_reason"] == "max_retries_exhausted"


def test_reconcile_skips_release_for_first_seat_verdict() -> None:
    snap = _snap_three_operator_proxy()
    watches = {
        "6885": {
            **_watch_incumbent(registration_id="reg-old"),
            "superseded_registration_id": PRIOR_NONE_REGISTRATION,
            "superseded_execution_id": PRIOR_NONE_EXECUTION,
            "predecessor_verdict": PredecessorVerdict.FIRST_SEAT_ON_LANE.value,
        }
    }

    with _enter_reconcile_patches(watches):
        result = reconcile_succession_confirmations(
            snapshot_reader=lambda: snap,
            release_fn=release_superseded_on_confirm,
        )

    assert len(result["confirmations"]) == 1
    assert result["releases"][0]["action"] == "skipped"
    assert result["releases"][0]["reason"] == "verdict_first_seat_on_lane"
    assert snap["admission_count"] == 3


def test_invariant8_three_rows_one_superseded_ends_at_two(tmp_path: Path) -> None:
    """AC4 — census of three operator-proxy rows; only recorded superseded is released."""
    from cdp_ask.execution_store import ExecutionStore

    store = ExecutionStore()

    async def _seed() -> dict[str, str]:
        ids: dict[str, str] = {}
        for label, purpose in (
            ("successor", "operator-proxy"),
            ("incumbent", "operator-proxy"),
            ("other_a", "operator-proxy"),
        ):
            rec = await store.create(holder="test", purpose=purpose)
            await store.attach_task(rec.execution_id, MagicMock(done=lambda: False))
            ids[label] = rec.execution_id
        return ids

    import asyncio

    ids = asyncio.run(_seed())

    async def _count() -> int:
        snap = await store.active_work_snapshot()
        return int(snap["admission_count"])

    assert asyncio.run(_count()) == 3

    client = MagicMock()
    client.poll.return_value = {
        "execution_id": ids["incumbent"],
        "status": "running",
        "streaming": False,
        "stop": False,
        "tool_pause": False,
    }
    client.abort.return_value = {
        "execution_id": ids["incumbent"],
        "status": "aborted",
        "aborted": True,
        "abort_outcome": "attested_stopped_and_deregistered",
    }

    handle = PredecessorHandle(
        registration_id="reg-old",
        execution_id=ids["incumbent"],
        verdict=PredecessorVerdict.INCUMBENT_RECORDED,
    )

    async def _terminalize() -> None:
        result = release_superseded_on_confirm(
            handle,
            client=client,
            idle_streak=_idle_streak_for_abort(),
        )
        assert result["action"] == "terminalized"
        await store.mark_terminal(ids["incumbent"], status="aborted", error="succession_superseded")

    asyncio.run(_terminalize())
    assert asyncio.run(_count()) == 2
