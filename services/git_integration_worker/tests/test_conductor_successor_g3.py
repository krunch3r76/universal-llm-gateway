"""G3 conductor successor without operator re-admit (FORK-1 / FORK-2)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
    conductor_has_live_nested,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (
    consult_pending_continue_owed,
    resolve_consult_summoning_watermark_at_instant,
)
from services.git_integration_worker.cursor_sdk_park import release_or_restore_for_child
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_CLOSEOUT = (
    Path(__file__).resolve().parent / "fixtures/operator_ear/12291_turn3_closeout.txt"
).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    dispatch_id = str(overrides.get("dispatch_id", "child-1"))
    base = {
        "thread_id": "9964",
        "model": "cursor/composer-2.5",
        "dispatch_id": dispatch_id,
        "execution_id": f"exec-{dispatch_id}",
        "message": f"msg-{dispatch_id}",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    contract: str = "implement",
    nest_under: str | None = None,
    work_key: str | None = None,
) -> None:
    wk = work_key or f"todo:g3-{req.dispatch_id}"
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
        contract=contract,
        source_repo="/repo",
        lease_key="/repo",
        work_key=wk,
        source_ref=wk,
    )
    patch: dict = {"contract": contract, "lane": "B"}
    if nest_under:
        patch["nest_under"] = nest_under
    ledger.merge_record_json(dispatch_id=req.dispatch_id, patch=patch)


def test_find_parked_parent_misses_terminal_parent() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-term"
    _admit(
        ledger,
        _req(dispatch_id=parent_id, execution_id="exec-parent"),
        contract="conductor",
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=?, park_child_dispatch_id=? "
            "WHERE dispatch_id=?",
            ("completed", "child-1", parent_id),
        )
    assert ledger.find_parked_parent_for_child(child_id="child-1") is None
    found = ledger.find_park_parent_any_status(child_id="child-1")
    assert found == (parent_id, "/repo", "completed")


@pytest.mark.asyncio
async def test_terminal_parent_child_mark_terminal_fires_parent_reactor() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-term"
    _admit(
        ledger,
        _req(dispatch_id=parent_id, execution_id="exec-parent"),
        contract="conductor",
    )
    _admit(ledger, _req())
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=?, park_child_dispatch_id=? "
            "WHERE dispatch_id=?",
            ("completed", "child-1", parent_id),
        )
    hop_mock = AsyncMock()
    from services.git_integration_worker.routes.cursor_sdk import (
        _mark_terminal_and_promote,
    )

    class _Ctl:
        worker_id = "w"

        def is_draining(self) -> bool:
            return False

    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.maybe_fire_conductor_hop_reactor",
        hop_mock,
    ):
        await _mark_terminal_and_promote(
            dispatch_id="child-1",
            terminal_status="completed",
            controller=_Ctl(),
            emit_tag="TEST",
        )
    hop_mock.assert_any_call(dispatch_id=parent_id)
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT park_child_dispatch_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    assert row["park_child_dispatch_id"] is None


def test_conductor_has_live_nested_grandchild() -> None:
    ledger = CursorDispatchLedger.instance()
    root = "root-1"
    mid = "mid-1"
    leaf = "leaf-1"
    _admit(ledger, _req(dispatch_id=root, execution_id="e-root"), contract="conductor")
    _admit(ledger, _req(dispatch_id=mid, execution_id="e-mid"), nest_under=root)
    _admit(ledger, _req(dispatch_id=leaf, execution_id="e-leaf"), nest_under=mid)
    ledger.mark_terminal(dispatch_id=root, terminal_status="completed")
    ledger.mark_terminal(dispatch_id=mid, terminal_status="completed")
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=? WHERE dispatch_id=?",
            ("running", leaf),
        )
    assert conductor_has_live_nested(dispatch_id=root) is True


@pytest.mark.asyncio
async def test_release_or_restore_terminal_parent_force_release() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-term"
    _admit(
        ledger,
        _req(dispatch_id=parent_id, execution_id="exec-parent"),
        contract="conductor",
    )
    _admit(ledger, _req())
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=?, park_child_dispatch_id=? "
            "WHERE dispatch_id=?",
            ("completed", "child-1", parent_id),
        )
    rel = AsyncMock()
    with patch(
        "services.git_integration_worker.cursor_sdk_park.force_release_sdk_dispatch_slot",
        rel,
    ):
        disposition = await release_or_restore_for_child(dispatch_id="child-1")
    assert disposition == "released"
    rel.assert_called_once()


def test_startup_reconcile_clears_stale_park_child() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-stale"
    _admit(
        ledger,
        _req(dispatch_id=parent_id, execution_id="exec-parent"),
        contract="conductor",
    )
    _admit(ledger, _req(dispatch_id="child-q", execution_id="exec-child-q"))
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=?, park_child_dispatch_id=? "
            "WHERE dispatch_id=?",
            ("completed", "child-q", parent_id),
        )
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=? WHERE dispatch_id=?",
            ("queued", "child-q"),
        )
    ledger.startup_reconcile(worker_instance="w1")
    with ledger._connect() as conn:
        parent = conn.execute(
            "SELECT park_child_dispatch_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
        child = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("child-q",),
        ).fetchone()
    assert parent["park_child_dispatch_id"] is None
    assert child["status"] == "failed"


def test_consult_pending_summoning_disjunct_uses_stamped_turn() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        _req(dispatch_id="pred-consult-g3", execution_id="exec-pc"),
        contract="conductor",
    )
    ledger.merge_record_json(
        dispatch_id="pred-consult-g3",
        patch={
            "closeout_body": _CLOSEOUT,
            "closeout_turn": 3,
            "closeout_stop_tokens": ["CONSULT_PENDING"],
            "summoning_thread_id": "13598",
            "consult_summoning_after_turn": 2,
        },
    )
    ledger.mark_terminal(dispatch_id="pred-consult-g3", terminal_status="completed")
    with ledger._connect() as conn:
        raw = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("pred-consult-g3",),
        ).fetchone()
    row = {k: raw[k] for k in raw.keys()}
    calls: list[tuple[str, int, str]] = []

    def _snap(tid: str, turn: int, agent: str) -> bool:
        calls.append((tid, turn, agent))
        return tid == "13598" and turn == 2

    assert consult_pending_continue_owed(row, reply_fn=_snap)
    assert ("13598", 2, "web-anthropic") in calls
    assert not any(c[1] == 5 and c[0] == "9964" for c in calls)


def test_resolve_watermark_empty_at_closeout_instant() -> None:
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest._fetch_thread_turns",
        return_value=[],
    ):
        assert (
            resolve_consult_summoning_watermark_at_instant(
                thread_id="13598",
                closeout_instant="2026-09-30T16:00:00+00:00",
            )
            == 0
        )


def test_summoning_retry_stamps_closeout_anchored_watermark() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        _req(dispatch_id="pred-retry", execution_id="exec-retry"),
        contract="conductor",
    )
    instant = "2026-09-30T16:00:00+00:00"
    ledger.merge_record_json(
        dispatch_id="pred-retry",
        patch={
            "closeout_body": _CLOSEOUT,
            "closeout_turn": 3,
            "closeout_stop_tokens": ["CONSULT_PENDING"],
            "summoning_thread_id": "13598",
            "consult_summoning_stamp_error": {
                "class": "empty_thread",
                "closeout_instant": instant,
            },
        },
    )
    ledger.mark_terminal(dispatch_id="pred-retry", terminal_status="completed")
    turns = [
        {
            "turn_number": 1,
            "created_at": "2026-09-30T17:00:00+00:00",
            "from_agent": "web-anthropic",
        }
    ]
    with ledger._connect() as conn:
        raw = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("pred-retry",),
        ).fetchone()
    row = {k: raw[k] for k in raw.keys()}

    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest._fetch_thread_turns",
        return_value=turns,
    ):
        assert consult_pending_continue_owed(
            row,
            reply_fn=lambda tid, turn, agent: tid == "13598" and turn == 0,
        )
    with ledger._connect() as conn:
        data = json.loads(
            conn.execute(
                "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                ("pred-retry",),
            ).fetchone()[0]
        )
    assert data.get("consult_summoning_after_turn") == 0
