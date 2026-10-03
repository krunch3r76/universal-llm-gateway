"""R7: conductor hop watchdog sweep (todo:conductor-hop-reactor)."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog import (
    maybe_fire_conductor_hop_watchdog,
    sweep_conductor_hop_watchdog,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
)
from services.git_integration_worker.cursor_sdk_park import (
    conductor_hop_watchdog_candidates,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_WORK_KEY = "todo:hop-watchdog-fixture"
_GRACE_S = 120.0


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONDUCTOR_HOP_REACTOR_GRACE_S", str(_GRACE_S))
    CursorDispatchLedger._instance = None
    yield tmp_path
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "9964",
        "model": "cursor/composer-2.5",
        "dispatch_id": "pred-watchdog-1",
        "execution_id": "exec-pred-watchdog-1",
        "message": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_conductor(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    hop_seq: int = 1,
) -> None:
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        contract="conductor",
        source_repo="/repo",
        lease_key="/repo",
        work_key=_WORK_KEY,
        source_ref=_WORK_KEY,
        hop_seq=hop_seq,
        hop_from="spawn-parent",
        hop_reason="spawn",
    )
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"contract": "conductor", "lane": "B"},
    )


def _terminal_row(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str = "pred-watchdog-1",
    closeout_tokens: list[str] | None = None,
    record_patch: dict | None = None,
    terminal_at_offset_s: float = -200.0,
    thread_id: str | None = None,
) -> dict:
    overrides: dict[str, object] = {"dispatch_id": dispatch_id}
    if thread_id is not None:
        overrides["thread_id"] = thread_id
        overrides["execution_id"] = f"exec-{dispatch_id}"
    req = _req(**overrides)
    _admit_conductor(ledger, req)
    patch: dict = {"summoning_thread_id": "9638"}
    if closeout_tokens is not None:
        patch["closeout_stop_tokens"] = closeout_tokens
    if record_patch:
        patch.update(record_patch)
    if patch:
        ledger.merge_record_json(dispatch_id=dispatch_id, patch=patch)
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    terminal_at = time.time() + terminal_at_offset_s
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"hop_last_terminal_at": terminal_at},
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    return {k: row[k] for k in row.keys()}


@pytest.mark.asyncio
async def test_watchdog_successor_body_carries_model_knobs() -> None:
    ledger = CursorDispatchLedger.instance()
    knobs = {"effort": "low", "fast": "true"}
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        record_patch={"model_knobs": knobs},
        terminal_at_offset_s=-200.0,
    )
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.hop_owed",
            return_value=True,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog._backoff_elapsed",
            return_value=True,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.post_conductor_hop_team_dispatch",
            AsyncMock(return_value=(True, {"dispatch_id": "succ-1"})),
        ) as post_mock,
    ):
        ok = await maybe_fire_conductor_hop_watchdog(dispatch_id="pred-watchdog-1")
    assert ok is True
    body = post_mock.await_args.args[0]
    assert body["model_knobs"] == knobs
    assert body["hop_reason"] == "watchdog"


def test_watchdog_candidate_false_before_grace() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"], terminal_at_offset_s=-30.0)
    assert conductor_hop_watchdog_candidates(ledger, grace_s=_GRACE_S) == []


def test_watchdog_candidate_true_after_grace_when_hop_owed() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"], terminal_at_offset_s=-200.0)
    assert conductor_hop_watchdog_candidates(ledger, grace_s=_GRACE_S) == [
        "pred-watchdog-1"
    ]


def test_watchdog_candidate_false_when_successor_stamped() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        record_patch={"hop_successor": "already-admitted"},
        terminal_at_offset_s=-200.0,
    )
    assert conductor_hop_watchdog_candidates(ledger, grace_s=_GRACE_S) == []


def test_statusless_transport_admit_error_is_not_permanent() -> None:
    """LB-1 — httpx / stargate_unreachable has no status_code; must stay transient."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog import (
        _admit_error_permanent,
    )
    from services.git_integration_worker.cursor_sdk_ledger_hop import merge_hop_patch

    merged = merge_hop_patch(
        "",
        {
            "hop_admit_error": {
                "last_error": "ConnectError",
                "reason": "stargate_unreachable",
            }
        },
    )
    admit_err = json.loads(merged)["hop_admit_error"]
    assert admit_err["retryable"] is True
    assert admit_err["last_status_code"] is None
    assert _admit_error_permanent({"record_json": merged}) is False


def test_merge_hop_admit_error_accumulates_attempts() -> None:
    from services.git_integration_worker.cursor_sdk_ledger_hop import merge_hop_patch

    merged = merge_hop_patch(
        "",
        {
            "hop_admit_error": {
                "last_error": "422",
                "last_status_code": 422,
            }
        },
    )
    merged = merge_hop_patch(
        merged,
        {
            "hop_admit_error": {
                "last_error": "422 again",
                "last_status_code": 422,
            }
        },
    )
    data = json.loads(merged)
    admit_err = data["hop_admit_error"]
    assert admit_err["attempts"] == 2
    assert admit_err["retryable"] is False


@pytest.mark.asyncio
async def test_watchdog_skips_permanent_admit_error_without_post() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        record_patch={
            "hop_admit_error": {
                "retryable": False,
                "attempts": 1,
                "last_status_code": 422,
                "last_error": "dispatch_thread_id required",
            }
        },
        terminal_at_offset_s=-200.0,
    )
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.post_conductor_hop_team_dispatch",
        AsyncMock(),
    ) as post_mock:
        ok = await maybe_fire_conductor_hop_watchdog(dispatch_id="pred-watchdog-1")
    assert ok is False
    post_mock.assert_not_called()


@pytest.mark.asyncio
async def test_watchdog_no_post_when_mission_parked() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        record_patch={
            "hop_parked": True,
            "hop_park_reason": "hop_budget_no_progress_cap",
        },
        terminal_at_offset_s=-200.0,
    )
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.post_conductor_hop_team_dispatch",
        AsyncMock(),
    ) as post_mock:
        ok = await maybe_fire_conductor_hop_watchdog(dispatch_id="pred-watchdog-1")
    assert ok is False
    post_mock.assert_not_called()


def test_watchdog_candidate_true_with_hop_admit_error_after_grace() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        record_patch={
            "hop_admit_error": {
                "error": "503",
                "status_code": 503,
            }
        },
        terminal_at_offset_s=-200.0,
    )
    assert conductor_hop_watchdog_candidates(ledger, grace_s=_GRACE_S) == [
        "pred-watchdog-1"
    ]


def test_watchdog_candidate_only_latest_terminal_row_on_thread() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(
        ledger,
        dispatch_id="pred-old",
        closeout_tokens=["ROW_HOP"],
        terminal_at_offset_s=-400.0,
    )
    _terminal_row(
        ledger,
        dispatch_id="pred-new",
        closeout_tokens=["ROW_HOP"],
        terminal_at_offset_s=-200.0,
    )
    assert conductor_hop_watchdog_candidates(ledger, grace_s=_GRACE_S) == ["pred-new"]


def _live_external_gate_snap(*, parent_thread: str = "9638") -> dict:
    return {
        "observed_at": "2026-09-05T00:00:00+00:00",
        "rows": [
            {
                "execution_id": "exec-ext-gate",
                "parent_thread": parent_thread,
                "stream_state": "running",
                "purpose": "operator-proxy",
            }
        ],
    }


@pytest.mark.asyncio
async def test_cdp_probe_indeterminate_admits_once_after_double_grace() -> None:
    """D2: empty snapshot withholds inside 2× grace, then admits once."""
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pred-watchdog-1"
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        record_patch={"closeout_harvest_owed": True},
        terminal_at_offset_s=-200.0,
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "succ-cdp-probe"}))
    snap = patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value={},
    )
    poster = patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.post_conductor_hop_team_dispatch",
        post,
    )
    with snap, poster:
        early = await maybe_fire_conductor_hop_watchdog(dispatch_id=dispatch_id)
    assert early is False
    post.assert_not_called()

    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"hop_last_terminal_at": time.time() - (2 * _GRACE_S + 30)},
    )
    with snap, poster:
        first = await maybe_fire_conductor_hop_watchdog(dispatch_id=dispatch_id)
        second = await maybe_fire_conductor_hop_watchdog(dispatch_id=dispatch_id)
    assert first is True
    assert second is False
    assert post.await_count == 1
    assert post.await_args.args[0]["hop_reason"] == "cdp_probe_indeterminate"
    with ledger._connect() as conn:
        stored = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    record = json.loads(stored["record_json"])
    assert isinstance(record.get("hop_cdp_probe_indeterminate_at"), (int, float))


@pytest.mark.asyncio
async def test_cdp_probe_indeterminate_live_gate_still_blocks() -> None:
    """D2: a live external gate stream is not released by the double-grace admit."""
    ledger = CursorDispatchLedger.instance()
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        record_patch={"closeout_harvest_owed": True},
        terminal_at_offset_s=-(2 * _GRACE_S + 30),
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "should-not-fire"}))
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
            return_value=_live_external_gate_snap(),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.post_conductor_hop_team_dispatch",
            post,
        ),
    ):
        fired = await maybe_fire_conductor_hop_watchdog(dispatch_id="pred-watchdog-1")
    assert fired is False
    post.assert_not_called()


def _record(ledger: CursorDispatchLedger, dispatch_id: str) -> dict:
    with ledger._connect() as conn:
        stored = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    return json.loads(stored["record_json"])


@pytest.mark.asyncio
async def test_cdp_probe_health_red_admits_at_grace_as_watchdog() -> None:
    """A 2s /health timeout is red, and must not hold a hop_owed row for 2× grace.

    observe_cdp_ask_health returns ok=False on that timeout. The one-shot CDP
    branch runs only when hop_owed is false; a readable snapshot still admits
    at reactor grace as watchdog.
    """
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pred-watchdog-1"
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        record_patch={"closeout_harvest_owed": True},
        terminal_at_offset_s=-200.0,
    )
    clear_snap = {"observed_at": "2026-09-05T00:00:00+00:00", "rows": []}
    post = AsyncMock(return_value=(True, {"dispatch_id": "succ-health-red"}))
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
            return_value=clear_snap,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.cdp_ask_health_red",
            return_value=True,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.post_conductor_hop_team_dispatch",
            post,
        ),
    ):
        first = await maybe_fire_conductor_hop_watchdog(dispatch_id=dispatch_id)
        second = await maybe_fire_conductor_hop_watchdog(dispatch_id=dispatch_id)
    assert first is True
    assert second is False
    assert post.await_count == 1
    assert post.await_args.args[0]["hop_reason"] == "watchdog"
    record = _record(ledger, dispatch_id)
    assert record.get("hop_successor") == "succ-health-red"
    assert "hop_cdp_probe_indeterminate_at" not in record


@pytest.mark.asyncio
async def test_cdp_probe_indeterminate_failed_post_does_not_burn_shot() -> None:
    """A 5xx leaves the row retryable. The one-shot stamp waits for success."""
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pred-watchdog-1"
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        record_patch={"closeout_harvest_owed": True},
        terminal_at_offset_s=-(2 * _GRACE_S + 30),
    )
    post = AsyncMock(return_value=(False, {"status_code": 503, "error": "unavailable"}))
    snap = patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value={},
    )
    poster = patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.post_conductor_hop_team_dispatch",
        post,
    )
    held = patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog._backoff_elapsed",
        return_value=False,
    )
    with snap, poster, held:
        blocked = await maybe_fire_conductor_hop_watchdog(dispatch_id=dispatch_id)
    assert blocked is False
    post.assert_not_called()
    assert "hop_cdp_probe_indeterminate_at" not in _record(ledger, dispatch_id)

    with snap, poster:
        failed = await maybe_fire_conductor_hop_watchdog(dispatch_id=dispatch_id)
    assert failed is False
    assert post.await_count == 1
    assert post.await_args.args[0]["hop_reason"] == "cdp_probe_indeterminate"
    record = _record(ledger, dispatch_id)
    assert "hop_cdp_probe_indeterminate_at" not in record
    assert record["hop_admit_error"]["retryable"] is True
    assert record["hop_admit_error"]["last_status_code"] == 503
    with snap:
        assert conductor_hop_watchdog_candidates(ledger, grace_s=_GRACE_S) == [
            dispatch_id
        ]

    post.return_value = (True, {"dispatch_id": "succ-after-503"})
    with snap, poster:
        retried = await maybe_fire_conductor_hop_watchdog(dispatch_id=dispatch_id)
    assert retried is True
    assert post.await_count == 2
    record = _record(ledger, dispatch_id)
    assert isinstance(record.get("hop_cdp_probe_indeterminate_at"), (int, float))
    assert record.get("hop_successor") == "succ-after-503"


@pytest.mark.asyncio
async def test_cdp_probe_indeterminate_permanent_error_stamps_and_drops() -> None:
    """A 422 is permanent: stamp the one-shot so the row leaves the candidate list."""
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pred-watchdog-1"
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        record_patch={"closeout_harvest_owed": True},
        terminal_at_offset_s=-(2 * _GRACE_S + 30),
    )
    post = AsyncMock(return_value=(False, {"status_code": 422, "error": "rejected"}))
    snap = patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value={},
    )
    poster = patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.post_conductor_hop_team_dispatch",
        post,
    )
    with snap, poster:
        failed = await maybe_fire_conductor_hop_watchdog(dispatch_id=dispatch_id)
        again = await maybe_fire_conductor_hop_watchdog(dispatch_id=dispatch_id)
    assert failed is False
    assert again is False
    assert post.await_count == 1
    record = _record(ledger, dispatch_id)
    assert record["hop_admit_error"]["retryable"] is False
    assert isinstance(record.get("hop_cdp_probe_indeterminate_at"), (int, float))
    with snap:
        assert conductor_hop_watchdog_candidates(ledger, grace_s=_GRACE_S) == []


@pytest.mark.asyncio
async def test_cdp_probe_indeterminate_crash_cap_parks_and_stamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admit-retry cap parks the row and burns the one-shot."""
    monkeypatch.setenv("CONDUCTOR_HOP_CRASH_CAP_PER_ROW", "1")
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pred-watchdog-1"
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        record_patch={"closeout_harvest_owed": True},
        terminal_at_offset_s=-(2 * _GRACE_S + 30),
    )
    post = AsyncMock(return_value=(False, {"status_code": 503, "error": "unavailable"}))
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
            return_value={},
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.post_conductor_hop_team_dispatch",
            post,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park.default_park_poster",
        ),
    ):
        failed = await maybe_fire_conductor_hop_watchdog(dispatch_id=dispatch_id)
    assert failed is False
    record = _record(ledger, dispatch_id)
    assert record.get("hop_parked") is True
    assert record.get("hop_park_reason") == "hop_budget_admit_retry_cap"
    assert isinstance(record.get("hop_cdp_probe_indeterminate_at"), (int, float))


def test_cdp_probe_due_checks_grace_before_network() -> None:
    """Terminal/grace are local. The lane read waits until twice reactor grace."""
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        record_patch={"closeout_harvest_owed": True},
        terminal_at_offset_s=-200.0,
    )
    from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
        cdp_probe_indeterminate_watchdog_due,
    )

    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.cdp_probe_indeterminate_withholds",
    ) as withholds:
        assert cdp_probe_indeterminate_watchdog_due(row) is False
    withholds.assert_not_called()


def test_cdp_probe_due_raise_does_not_abort_candidate_sweep() -> None:
    """One row's probe raise returns false; the next row still becomes a candidate."""
    ledger = CursorDispatchLedger.instance()
    boom = _terminal_row(
        ledger,
        dispatch_id="pred-boom",
        closeout_tokens=["ROW_HOP"],
        record_patch={"closeout_harvest_owed": True},
        terminal_at_offset_s=-(2 * _GRACE_S + 30),
        thread_id="9964",
    )
    ok = _terminal_row(
        ledger,
        dispatch_id="pred-ok",
        closeout_tokens=["ROW_HOP"],
        record_patch={"closeout_harvest_owed": True},
        terminal_at_offset_s=-(2 * _GRACE_S + 30),
        thread_id="9965",
    )

    def _withholds(row: dict) -> bool:
        if str(row.get("dispatch_id") or "") == "pred-boom":
            raise RuntimeError("probe down")
        return True

    with (
        patch(
            "services.git_integration_worker.cursor_sdk_park._latest_terminal_conductor_rows",
            return_value=[boom, ok],
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
            return_value={},
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.cdp_probe_indeterminate_withholds",
            side_effect=_withholds,
        ),
    ):
        assert conductor_hop_watchdog_candidates(ledger, grace_s=_GRACE_S) == [
            "pred-ok"
        ]


def test_cdp_probe_watchdog_candidate_follows_double_grace() -> None:
    """Sweep candidates stay empty until twice the reactor grace."""
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pred-watchdog-1"
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        record_patch={"closeout_harvest_owed": True},
        terminal_at_offset_s=-200.0,
    )
    snap = patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value={},
    )
    with snap:
        assert conductor_hop_watchdog_candidates(ledger, grace_s=_GRACE_S) == []
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"hop_last_terminal_at": time.time() - (2 * _GRACE_S + 30)},
    )
    with snap:
        assert conductor_hop_watchdog_candidates(ledger, grace_s=_GRACE_S) == [
            dispatch_id
        ]


@pytest.mark.asyncio
async def test_maybe_fire_watchdog_posts_with_watchdog_reason() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"], terminal_at_offset_s=-200.0)
    captured: dict = {}

    async def _capture(body, **kwargs):
        captured["body"] = body
        return True, {"dispatch_id": "succ-watchdog-2"}

    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.post_conductor_hop_team_dispatch",
        AsyncMock(side_effect=_capture),
    ):
        ok = await maybe_fire_conductor_hop_watchdog(dispatch_id="pred-watchdog-1")
    assert ok is True
    assert captured["body"]["hop_reason"] == "watchdog"
    assert (
        captured["body"]["generation_options"]["idempotency_key"]
        == "conductor-hop:pred-watchdog-1"
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-watchdog-1'"
        ).fetchone()
    fields = hop_fields_from_record_json(row["record_json"])
    assert fields.get("hop_successor") == "succ-watchdog-2"


@pytest.mark.asyncio
async def test_sweep_emits_watchdog_fired_event() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"], terminal_at_offset_s=-200.0)
    emitted: list[str] = []

    def _capture(signal: str, **_kwargs):
        emitted.append(signal)

    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.post_conductor_hop_team_dispatch",
            AsyncMock(return_value=(True, {"dispatch_id": "succ-watchdog-3"})),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.emit_frontier_sdk_conductor_hop_watchdog_fired",
            side_effect=lambda **kw: emitted.append("watchdog_fired"),
        ),
    ):
        fired = await sweep_conductor_hop_watchdog(ledger)
    assert fired == 1
    assert "watchdog_fired" in emitted


@pytest.mark.asyncio
async def test_watchdog_fires_consult_pending_before_park_harvest_continue() -> None:
    """D3: consult continue branch fires before park_harvest continue."""
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="pred-consult-watch-1")
    _admit_conductor(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"closeout_turn": 3, "closeout_stop_tokens": ["CONSULT_PENDING"]},
    )
    ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")

    consult_mock = AsyncMock(return_value=True)
    continue_mock = AsyncMock(return_value=True)

    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.consult_pending_continue_owed",
            return_value=True,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.fire_consult_pending_continue",
            consult_mock,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.park_harvest_continue_owed",
            return_value=True,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.fire_park_harvest_continue",
            continue_mock,
        ),
    ):
        ok = await maybe_fire_conductor_hop_watchdog(dispatch_id=req.dispatch_id)
    assert ok is True
    consult_mock.assert_awaited_once()
    continue_mock.assert_not_called()


@pytest.mark.asyncio
async def test_watchdog_fires_park_harvest_continue_before_arm_recipe() -> None:
    """R-6: continue branch fires before park_harvest arm-recipe."""
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="pred-continue-1")
    _admit_conductor(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={
            "closeout_stop_tokens": ["PARKED_TRANSPORT", "CONSULT_PENDING"],
            "closeout_turn": 48,
            "closeout_harvest_owed": True,
            "hop_park_harvest_fired_at": time.time(),
        },
    )
    ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")

    continue_mock = AsyncMock(return_value=True)

    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.park_harvest_continue_owed",
            return_value=True,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.fire_park_harvest_continue",
            continue_mock,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.park_harvest_owed",
            return_value=True,
        ) as arm_mock,
    ):
        ok = await maybe_fire_conductor_hop_watchdog(dispatch_id=req.dispatch_id)
    assert ok is True
    continue_mock.assert_awaited_once()
    arm_mock.assert_not_called()


@pytest.mark.asyncio
async def test_watchdog_parks_on_budget_exhaustion() -> None:
    ledger = CursorDispatchLedger.instance()
    for idx in range(24):
        dispatch_id = f"pred-cap-{idx}"
        _terminal_row(
            ledger,
            dispatch_id=dispatch_id,
            closeout_tokens=["ROW_HOP"],
            terminal_at_offset_s=-200.0 - idx,
        )
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.park_conductor_hop_mission",
        AsyncMock(),
    ) as park_mock:
        ok = await maybe_fire_conductor_hop_watchdog(dispatch_id="pred-cap-0")
    assert ok is False
    park_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_watchdog_does_not_budget_park_cancel_discard() -> None:
    """a:37149 — discard at mission_cap must not page/lock via watchdog budget.

    Reviewer falsifier: discard row past grace → sweep/fire must not call
    ``park_conductor_hop_mission`` and must leave ``mission_park_state`` clear.
    """
    from services.git_integration_worker.cursor_sdk_conductor_park_gate import (
        mission_park_state,
    )

    ledger = CursorDispatchLedger.instance()
    for idx in range(24):
        dispatch_id = f"pred-disc-{idx}"
        _terminal_row(
            ledger,
            dispatch_id=dispatch_id,
            closeout_tokens=["ROW_HOP"] if idx else [],
            terminal_at_offset_s=-200.0 - idx,
        )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_kind='cancel_discard', "
            "status='cancelled', terminal_status='cancelled' "
            "WHERE dispatch_id='pred-disc-0'"
        )
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog."
        "park_conductor_hop_mission",
        AsyncMock(),
    ) as park_mock:
        ok = await maybe_fire_conductor_hop_watchdog(dispatch_id="pred-disc-0")
    assert ok is False
    park_mock.assert_not_called()
    with ledger._connect() as conn:
        assert mission_park_state(conn, work_key=_WORK_KEY) is None
    assert "pred-disc-0" not in conductor_hop_watchdog_candidates(
        ledger, grace_s=0.0, now=time.time() + 10_000
    )


@pytest.mark.asyncio
async def test_sweep_consult_pending_continue_once() -> None:
    """Bare CONSULT_PENDING enters the sweep union and admits once."""
    ledger = CursorDispatchLedger.instance()
    closeout = "stop: CONSULT_PENDING\nstatus: blocked\n"
    dispatch_id = "pred-consult-sweep-1"
    _terminal_row(
        ledger,
        dispatch_id=dispatch_id,
        closeout_tokens=["CONSULT_PENDING"],
        record_patch={
            "closeout_body": closeout,
            "closeout_turn": 3,
            "closeout_stop_tokens": ["CONSULT_PENDING"],
        },
        thread_id="12291",
    )
    post_mock = AsyncMock(return_value=(True, {"dispatch_id": "succ-consult-sweep"}))
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.reply_arrived_on_thread",
            return_value=True,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            post_mock,
        ),
    ):
        fired = await sweep_conductor_hop_watchdog(ledger)
        again = await sweep_conductor_hop_watchdog(ledger)
    assert fired == 1
    assert again == 0
    assert post_mock.await_count == 1


def test_watchdog_hops_owed_off_event_loop() -> None:
    """hop_owed does sync CDP HTTP — must not run on the GIW asyncio thread."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "cursor_sdk_closeout"
        / "conductor_hop_watchdog.py"
    ).read_text(encoding="utf-8")
    assert "await asyncio.to_thread(hop_owed," in source
