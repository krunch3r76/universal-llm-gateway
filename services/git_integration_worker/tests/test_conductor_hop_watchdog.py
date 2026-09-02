"""R7: conductor hop watchdog sweep (todo:conductor-hop-reactor)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog import (
    maybe_fire_conductor_hop_watchdog,
    sweep_conductor_hop_watchdog,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import hop_fields_from_record_json
from services.git_integration_worker.cursor_sdk_park import conductor_hop_watchdog_candidates
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
        contract="light-bounded",
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
        patch={"packet_kind": "conductor", "lane": "B"},
    )


def _terminal_row(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str = "pred-watchdog-1",
    closeout_tokens: list[str] | None = None,
    record_patch: dict | None = None,
    terminal_at_offset_s: float = -200.0,
) -> dict:
    req = _req(dispatch_id=dispatch_id)
    _admit_conductor(ledger, req)
    patch: dict = {}
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

    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.post_conductor_hop_team_dispatch",
        AsyncMock(return_value=(True, {"dispatch_id": "succ-watchdog-3"})),
    ), patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.emit_frontier_sdk_conductor_hop_watchdog_fired",
        side_effect=lambda **kw: emitted.append("watchdog_fired"),
    ):
        fired = await sweep_conductor_hop_watchdog(ledger)
    assert fired == 1
    assert "watchdog_fired" in emitted


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
