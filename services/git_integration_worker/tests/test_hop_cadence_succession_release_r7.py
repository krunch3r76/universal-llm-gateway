"""R7 — terminalize superseded execution on succession confirm (arc 7119)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
    PRIOR_NONE_EXECUTION,
    PRIOR_NONE_REGISTRATION,
    PredecessorHandle,
    PredecessorVerdict,
)
from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
    reconcile_succession_confirmations,
)
from services.git_integration_worker.cursor_auto.hop_cadence_succession_release import (
    predecessor_mid_turn,
    release_superseded_on_confirm,
)

pytestmark = pytest.mark.offline

_INCUMBENT_EXEC = "exec-incumbent-r7"
_SUCCESSOR_EXEC = "exec-successor-r7"
_OTHER_A = "exec-other-a"
_OTHER_B = "exec-other-b"


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


def _poll(*, status: str = "running", streaming: bool = False, tool_pause: bool = False) -> dict[str, Any]:
    return {
        "execution_id": _INCUMBENT_EXEC,
        "status": status,
        "streaming": streaming,
        "tool_pause": tool_pause,
    }


def _client(*, poll: dict[str, Any] | None = None, abort: dict[str, Any] | None = None) -> MagicMock:
    client = MagicMock()
    client.poll.return_value = poll or _poll()
    client.abort.return_value = abort or {"execution_id": _INCUMBENT_EXEC, "status": "aborted", "aborted": True}
    return client


def test_predecessor_mid_turn_streaming() -> None:
    mid, reason = predecessor_mid_turn(_poll(streaming=True))
    assert mid is True
    assert reason == "predecessor_streaming"


def test_predecessor_mid_turn_tool_pause() -> None:
    mid, reason = predecessor_mid_turn(_poll(tool_pause=True))
    assert mid is True
    assert reason == "predecessor_tool_pause"


def test_predecessor_mid_turn_idle_running() -> None:
    mid, reason = predecessor_mid_turn(_poll())
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


def test_release_terminalizes_incumbent_recorded() -> None:
    client = _client()
    result = release_superseded_on_confirm(_handle(), client=client)
    assert result["action"] == "terminalized"
    assert result["execution_id"] == _INCUMBENT_EXEC
    client.poll.assert_called_once_with(_INCUMBENT_EXEC)
    client.abort.assert_called_once_with(_INCUMBENT_EXEC)


def test_release_deferred_when_streaming() -> None:
    client = _client(poll=_poll(streaming=True))
    result = release_superseded_on_confirm(_handle(), client=client)
    assert result["action"] == "deferred"
    assert result["reason"] == "predecessor_streaming"
    client.abort.assert_not_called()


def test_release_deferred_when_tool_pause() -> None:
    client = _client(poll=_poll(tool_pause=True))
    result = release_superseded_on_confirm(_handle(), client=client)
    assert result["action"] == "deferred"
    assert result["reason"] == "predecessor_tool_pause"
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


def _watch_incumbent() -> dict[str, Any]:
    return {
        "thread_id": "6885",
        "registration_id": "reg-old",
        "successor_execution_id": "stargate-uuid",
        "pending_satellite_execution_id": _SUCCESSOR_EXEC,
        "superseded_registration_id": "reg-old",
        "superseded_execution_id": _INCUMBENT_EXEC,
        "predecessor_verdict": PredecessorVerdict.INCUMBENT_RECORDED.value,
    }


def test_reconcile_releases_only_incumbent_recorded() -> None:
    snap = _snap_three_operator_proxy()
    watches = {"6885": _watch_incumbent()}
    terminalized: list[str] = []

    def _release(handle: PredecessorHandle) -> dict[str, Any]:
        if handle.verdict == PredecessorVerdict.INCUMBENT_RECORDED:
            terminalized.append(handle.execution_id)
            snap["admission_count"] -= 1
            snap["rows"] = [r for r in snap["rows"] if r["execution_id"] != handle.execution_id]
            return {"action": "terminalized", "execution_id": handle.execution_id}
        return {"action": "skipped", "reason": f"verdict_{handle.verdict.value}"}

    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.load_watches",
        side_effect=lambda path=None: watches,
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.save_watches",
        side_effect=lambda data, path=None: watches.update(data) or None,
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_succession_confirmed",
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_registration_advanced",
    ):
        result = reconcile_succession_confirmations(
            snapshot_reader=lambda: snap,
            release_fn=_release,
        )

    assert len(result["confirmations"]) == 1
    assert terminalized == [_INCUMBENT_EXEC]
    assert snap["admission_count"] == 2
    assert len(snap["rows"]) == 3
    assert result["releases"][0]["action"] == "terminalized"


def test_reconcile_skips_release_for_first_seat_verdict() -> None:
    snap = _snap_three_operator_proxy()
    watches = {
        "6885": {
            **_watch_incumbent(),
            "superseded_registration_id": PRIOR_NONE_REGISTRATION,
            "superseded_execution_id": PRIOR_NONE_EXECUTION,
            "predecessor_verdict": PredecessorVerdict.FIRST_SEAT_ON_LANE.value,
        }
    }

    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.load_watches",
        side_effect=lambda path=None: watches,
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.save_watches",
        side_effect=lambda data, path=None: watches.update(data) or None,
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_succession_confirmed",
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_registration_advanced",
    ):
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
        "tool_pause": False,
    }
    client.abort.return_value = {"execution_id": ids["incumbent"], "status": "aborted"}

    handle = PredecessorHandle(
        registration_id="reg-old",
        execution_id=ids["incumbent"],
        verdict=PredecessorVerdict.INCUMBENT_RECORDED,
    )

    async def _terminalize() -> None:
        result = release_superseded_on_confirm(handle, client=client)
        assert result["action"] == "terminalized"
        await store.mark_terminal(ids["incumbent"], status="aborted", error="succession_superseded")

    asyncio.run(_terminalize())
    assert asyncio.run(_count()) == 2
