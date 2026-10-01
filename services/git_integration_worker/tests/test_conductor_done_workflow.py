"""A9: conductor DONE closeout sets todo workflow_state when the fold is closed."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from implement_admission.closeout_runtime import (
    CloseoutRuntime,
    reset_runtime,
    set_runtime,
)

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    merge_conductor_closeout_hop_authority,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_WORK_KEY = "todo:conductor-done-workflow"


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None
    reset_runtime()


def _admit() -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="14001",
        model="cursor/composer-2.5",
        dispatch_id="done-wf-1",
        execution_id="exec-done-wf-1",
        message="conductor",
    )
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
        hop_seq=1,
        hop_from="spawn-parent",
        hop_reason="spawn",
    )
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"contract": "conductor", "lane": "B"},
    )


def _fold(statuses: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(row_status=statuses)


def _run(monkeypatch: pytest.MonkeyPatch, *, body: str, statuses: dict[str, str]) -> list:
    calls: list[tuple[str, dict]] = []

    def _dispatch(tool: str, args: dict) -> dict:
        calls.append((tool, args))
        return {}

    set_runtime(CloseoutRuntime(dispatch=_dispatch))
    monkeypatch.setattr(
        "implement_admission.conductor_witness.fold_scoreboard",
        lambda *a, **k: _fold(statuses),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_nested_witness.fold_deps_with_ledger",
        lambda *a, **k: None,
    )
    _admit()
    merge_conductor_closeout_hop_authority(
        dispatch_id="done-wf-1",
        closeout_body=body,
        thread_id="14001",
    )
    return calls


_DONE_BODY = "status: complete\nstop: DONE\n"


def test_done_closeout_marks_todo_when_every_row_done_and_g7_landed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _run(
        monkeypatch,
        body=_DONE_BODY,
        statuses={"G1": "DONE", "G6": "DONE", "G7": "DONE"},
    )
    assert ("entity_update", {"entity_id": _WORK_KEY, "workflow_state": "done"}) in calls


def test_done_closeout_skips_when_ladder_partial_or_g7_open_or_row_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partial = _run(
        monkeypatch,
        body=_DONE_BODY,
        statuses={"G1": "DONE", "G7": "OPEN"},
    )
    assert not any(tool == "entity_update" for tool, _ in partial)

    CursorDispatchLedger._instance = None
    open_g7 = _run(
        monkeypatch,
        body=_DONE_BODY,
        statuses={"G1": "DONE", "G6": "OPEN", "G7": "DONE"},
    )
    assert not any(tool == "entity_update" for tool, _ in open_g7)

    CursorDispatchLedger._instance = None
    hopped = _run(
        monkeypatch,
        body="status: complete\nstop: ROW_HOP\nhop_seq: 1\n",
        statuses={"G1": "DONE", "G7": "DONE"},
    )
    assert not any(tool == "entity_update" for tool, _ in hopped)


class _Ctl:
    worker_id = "w"

    def is_draining(self) -> bool:
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entity_update_behavior",
    [
        "raise",
        "error_response",
    ],
)
async def test_entity_update_failure_still_reaches_mark_terminal(
    monkeypatch: pytest.MonkeyPatch,
    entity_update_behavior: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Raising or error entity_update must not escape before mark_terminal."""
    from services.git_integration_worker.routes.cursor_sdk import (
        _mark_terminal_and_promote,
    )

    def _dispatch(tool: str, args: dict) -> dict:
        if tool == "entity_update":
            if entity_update_behavior == "raise":
                raise RuntimeError("cortex down")
            return {"error": "write rejected"}
        return {}

    set_runtime(CloseoutRuntime(dispatch=_dispatch))
    monkeypatch.setattr(
        "implement_admission.conductor_witness.fold_scoreboard",
        lambda *a, **k: _fold({"G1": "DONE", "G6": "DONE", "G7": "DONE"}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_nested_witness.fold_deps_with_ledger",
        lambda *a, **k: None,
    )
    _admit()
    with caplog.at_level("WARNING"):
        merge_conductor_closeout_hop_authority(
            dispatch_id="done-wf-1",
            closeout_body=_DONE_BODY,
            thread_id="14001",
        )
        with patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.maybe_fire_conductor_hop_reactor",
            new_callable=AsyncMock,
        ):
            await _mark_terminal_and_promote(
                dispatch_id="done-wf-1",
                terminal_status="completed",
                controller=_Ctl(),
                emit_tag="CURSOR_TEST_DONE_WF",
            )

    assert _WORK_KEY in caplog.text
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("done-wf-1",),
        ).fetchone()
    assert row is not None
    assert row["status"] == "completed"
