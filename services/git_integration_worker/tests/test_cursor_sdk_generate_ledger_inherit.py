"""Outstanding CDP generate ids inherit across resume_of (friction:37401)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.mcp_bridge_generate_ledger import (
    GenerateObserver,
    read_generate_records,
    read_received_execution_ids,
)
from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_await_reply import (
    ADMITTED_VIA_AWAIT_RESUME,
    AWAIT_REPLY_FLAG_ENV,
    PARK_KIND_AWAIT_REPLY,
    maybe_await_park_at_terminal,
    outstanding_generates,
)
from services.git_integration_worker.cursor_sdk_generate_ledger_inherit import (
    inherit_outstanding_generates,
)
from services.git_integration_worker.cursor_sdk_park_resume import (
    ADMITTED_VIA_PARK_RESUME,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

_EXEC = "3f6a5a66-e9d3-4c55-886b-000000000001"
_EXEC2 = "e3c9f4d6-aaaa-4c55-886b-000000000002"
_THREAD = "14692"


def _spool(tmp_path: Path) -> Path:
    spool = tmp_path / "steer-spool"
    spool.mkdir(parents=True, exist_ok=True)
    return spool


def _fire(
    tmp_path: Path,
    dispatch_id: str,
    *,
    execution_id: str = _EXEC,
    after_turn: int = 3,
) -> None:
    row = {
        "execution_id": execution_id,
        "thread_id": _THREAD,
        "after_turn": after_turn,
        "from_agent": "web-anthropic",
        "model": "cdp/opus-5.5",
        "fired_at": datetime.now(UTC).isoformat(),
    }
    path = _spool(tmp_path) / f"{dispatch_id}.cdp-generates.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _received(
    tmp_path: Path, dispatch_id: str, *, execution_id: str = _EXEC
) -> None:
    row = {
        "kind": "received",
        "execution_id": execution_id,
        "tool": "wait",
        "received_at": datetime.now(UTC).isoformat(),
    }
    path = _spool(tmp_path) / f"{dispatch_id}.cdp-generates.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _seed_running(dispatch_id: str, *, tmp_path: Path) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="7040",
        model="cursor/grok-4.7",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        caller_agent="cursor",
        message=f"packet {dispatch_id}",
        handoff_contract="freeform",
        lane="B",
        worktree_isolated=True,
        worktree_path=str(tmp_path / f"wt-{dispatch_id}"),
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="grok-4.7",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id="m",
        ),
        contract="freeform",
        source_repo=str(load_config().source_repo),
        lease_key=f"lease-{dispatch_id}",
    )
    ledger.mark_running(dispatch_id=dispatch_id)


def test_copies_outstanding_not_already_received(tmp_path: Path) -> None:
    """Parent fired two; one received — child ledger gets only the outstanding id."""
    spool = _spool(tmp_path)
    _fire(tmp_path, "parent-p", execution_id=_EXEC)
    _received(tmp_path, "parent-p", execution_id=_EXEC)
    _fire(tmp_path, "parent-p", execution_id=_EXEC2)
    copied = inherit_outstanding_generates(
        parent_id="parent-p",
        child_id="parent-p-r1",
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        spool_dir=spool,
    )
    assert copied == [_EXEC2]
    assert [
        r["execution_id"]
        for r in read_generate_records("parent-p-r1", spool_dir=spool)
    ] == [_EXEC2]
    assert outstanding_generates("parent-p-r1", spool_dir=spool)[0].execution_id == (
        _EXEC2
    )


def test_idempotent_second_call_does_not_duplicate(tmp_path: Path) -> None:
    """Replay after crash-between-insert-and-copy must not append twice."""
    spool = _spool(tmp_path)
    _fire(tmp_path, "parent-p")
    first = inherit_outstanding_generates(
        parent_id="parent-p",
        child_id="parent-p-r1",
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        spool_dir=spool,
    )
    second = inherit_outstanding_generates(
        parent_id="parent-p",
        child_id="parent-p-r1",
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        spool_dir=spool,
    )
    assert first == [_EXEC]
    assert second == []
    assert [
        r["execution_id"]
        for r in read_generate_records("parent-p-r1", spool_dir=spool)
    ] == [_EXEC]


def test_skips_await_reply_resume_to_avoid_repark_loop(tmp_path: Path) -> None:
    """Await-resume preamble already carries the reply; copying would re-park."""
    spool = _spool(tmp_path)
    _fire(tmp_path, "parent-p")
    copied = inherit_outstanding_generates(
        parent_id="parent-p",
        child_id="parent-p-c1",
        admitted_via=ADMITTED_VIA_AWAIT_RESUME,
        spool_dir=spool,
    )
    assert copied == []
    assert not (_spool(tmp_path) / "parent-p-c1.cdp-generates.jsonl").exists()


def test_missing_parent_spool_is_empty_not_an_error(tmp_path: Path) -> None:
    """Partial failure: parent JSONL gone (or never written) — fail open."""
    copied = inherit_outstanding_generates(
        parent_id="missing-parent",
        child_id="missing-parent-r1",
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        spool_dir=_spool(tmp_path),
    )
    assert copied == []


def test_child_observer_can_mark_inherited_generate_received(
    tmp_path: Path,
) -> None:
    """Receive path: child's empty known-set is the drop; inherit fills it."""
    spool = _spool(tmp_path)
    _fire(tmp_path, "parent-p")
    inherit_outstanding_generates(
        parent_id="parent-p",
        child_id="parent-p-r1",
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        spool_dir=spool,
    )
    observer = GenerateObserver(dispatch_id="parent-p-r1", spool_dir=spool)
    observer.on_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "agent_bus",
                "arguments": {
                    "tool": "wait",
                    "arguments": {
                        "thread": _THREAD,
                        "after_turn": 3,
                        "execution_id": _EXEC,
                        "from_agent": "web-anthropic",
                    },
                },
            },
        }
    )
    observer.on_result(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "complete": True,
                                "qualifying_reply_turn": 4,
                                "producer": {
                                    "execution_id": _EXEC,
                                    "state": "terminal",
                                },
                            }
                        ),
                    }
                ],
                "isError": False,
            },
        }
    )
    assert read_received_execution_ids("parent-p-r1", spool_dir=spool) == {_EXEC}
    assert outstanding_generates("parent-p-r1", spool_dir=spool) == []


@pytest.mark.asyncio
async def test_child_closeout_parks_when_inherited_generate_still_outstanding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abandon path: child never waits — closeout must still await-park."""
    monkeypatch.setenv(AWAIT_REPLY_FLAG_ENV, "1")
    spool = _spool(tmp_path)
    _fire(tmp_path, "parent-p")
    inherit_outstanding_generates(
        parent_id="parent-p",
        child_id="parent-p-r1",
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        spool_dir=spool,
    )
    _seed_running("parent-p-r1", tmp_path=tmp_path)
    parked = await maybe_await_park_at_terminal(
        dispatch_id="parent-p-r1",
        thread_id="7040",
        execution_id="exec-parent-p-r1",
        bus=MagicMock(),
        spool_dir=spool,
    )
    assert parked is True
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT park_kind FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("parent-p-r1",),
        ).fetchone()
    assert row["park_kind"] == PARK_KIND_AWAIT_REPLY
