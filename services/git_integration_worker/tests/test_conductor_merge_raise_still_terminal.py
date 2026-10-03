"""friction 37404: hop-authority merge raise must not skip the terminal mark."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_park_for_restart import ParkMark
from services.git_integration_worker.routes import cursor_sdk as route_mod
from services.git_integration_worker.tests.test_conductor_closeout_terminate_order import (
    _admit_running_conductor,
    _controller,
    _install_closeout_stubs,
    _outcome,
    _req,
)

pytestmark = pytest.mark.offline


def _status(dispatch_id: str) -> str:
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    assert row is not None
    return str(row["status"])


def _counting_raise(calls: list[str]):
    def _raise_merge(**_kw: Any) -> None:
        calls.append("merge-raise")
        raise RuntimeError("hop merge boom")

    return _raise_merge


@pytest.mark.asyncio
async def test_deliver_sdk_closeout_merge_raise_still_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = _req(dispatch_id="merge-raise-deliver", execution_id="exec-merge-raise-d")
    _admit_running_conductor(req, tmp_path)
    call_order: list[str] = []
    _install_closeout_stubs(
        monkeypatch, closeout_body="status: complete\n", call_order=call_order
    )
    raise_calls: list[str] = []
    monkeypatch.setattr(
        route_mod,
        "merge_conductor_closeout_hop_authority",
        _counting_raise(raise_calls),
    )
    monkeypatch.setattr(route_mod, "_terminate_link", AsyncMock())
    bus = MagicMock()
    bus.reply = AsyncMock(
        return_value=MagicMock(status_code=201, body={"turn_number": 2})
    )

    await route_mod._deliver_sdk_closeout(
        req=req,
        source_repo=tmp_path / "repo",
        outcome=_outcome("status: complete\n"),
        degraded_reason=None,
        bus=bus,
        reply_to="dispatch",
        work_item_ref="todo:conductor-closeout-completes-open-mission",
        controller=_controller(),
        packet_text="---\ncontract: conductor\n---\n",
    )

    assert raise_calls == ["merge-raise"]
    assert "promote" in call_order
    assert _status(req.dispatch_id) == "completed"


@pytest.mark.asyncio
async def test_finalize_parked_merge_raise_still_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.cursor_sdk_closeout.park_finalize import (
        finalize_parked,
    )
    from services.git_integration_worker.cursor_sdk_park_for_restart import ParkMark

    req = _req(
        dispatch_id="merge-raise-park",
        execution_id="exec-merge-raise-p",
        thread_id="9011",
    )
    _admit_running_conductor(req, tmp_path)
    mark = ParkMark(
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        intent_id="intent-37404",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="2026-10-01T00:00:00Z",
        method="run_cancel",
        marked_at=0.0,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize."
        "emit_partial_harvest_on_park",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize.mark_parked",
        lambda **_kw: None,
    )
    raise_calls: list[str] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize."
        "merge_conductor_closeout_hop_authority",
        _counting_raise(raise_calls),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize."
        "emit_sdk_park_parked",
        lambda **_kw: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize.terminal_emitted",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize.clear_park_mark",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(route_mod, "_promote_queued_for_lease", AsyncMock())
    monkeypatch.setattr(
        route_mod, "maybe_prune_worktree_on_terminal", lambda **_kw: None
    )
    monkeypatch.setattr(route_mod, "_terminate_link", AsyncMock())
    bus = MagicMock()
    bus.reply = AsyncMock(
        return_value=MagicMock(status_code=201, body={"turn_number": 3})
    )

    await finalize_parked(
        req=req,
        source_repo=tmp_path / "repo",
        bus=bus,
        reply_to="dispatch",
        controller=_controller(),
        mark=mark,
        outcome=_outcome(""),
        exc=None,
    )

    assert raise_calls == ["merge-raise"]
    assert _status(req.dispatch_id) == "cancelled"


def _park_mark(dispatch_id: str, thread_id: str) -> ParkMark:
    from services.git_integration_worker.cursor_sdk_park_for_restart import (
        _marks,
        reset_park_marks,
    )

    reset_park_marks()
    mark = ParkMark(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        intent_id="intent-37491",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="2026-10-01T00:00:00Z",
        method="run_cancel",
        marked_at=0.0,
    )
    _marks[dispatch_id] = mark
    return mark


def _stub_park_finalize_preamble(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize."
        "emit_partial_harvest_on_park",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize.mark_parked",
        lambda **_kw: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize."
        "emit_sdk_park_parked",
        lambda **_kw: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize.terminal_emitted",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(route_mod, "_promote_queued_for_lease", AsyncMock())
    monkeypatch.setattr(
        route_mod, "maybe_prune_worktree_on_terminal", lambda **_kw: None
    )
    monkeypatch.setattr(route_mod, "_terminate_link", AsyncMock())


@pytest.mark.asyncio
async def test_finalize_parked_merge_cancelled_still_promotes_and_clears_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """friction 37491: task cancel during hop merge still promotes and drops the mark."""
    from services.git_integration_worker.cursor_sdk_closeout.park_finalize import (
        finalize_parked,
    )
    from services.git_integration_worker.cursor_sdk_park_for_restart import park_mark

    req = _req(
        dispatch_id="merge-cancel-park",
        execution_id="exec-merge-cancel-p",
        thread_id="9012",
    )
    _admit_running_conductor(req, tmp_path)
    mark = _park_mark(req.dispatch_id, req.thread_id)
    _stub_park_finalize_preamble(monkeypatch)
    entered = threading.Event()
    released = threading.Event()

    def _block_merge(**_kw: Any) -> None:
        entered.set()
        released.wait(timeout=30)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize."
        "merge_conductor_closeout_hop_authority",
        _block_merge,
    )
    bus = MagicMock()
    bus.reply = AsyncMock(
        return_value=MagicMock(status_code=201, body={"turn_number": 3})
    )

    task = asyncio.create_task(
        finalize_parked(
            req=req,
            source_repo=tmp_path / "repo",
            bus=bus,
            reply_to="dispatch",
            controller=_controller(),
            mark=mark,
            outcome=_outcome(""),
            exc=None,
        )
    )
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await task
    released.set()
    assert park_mark(req.dispatch_id) is None
    assert _status(req.dispatch_id) == "cancelled"


@pytest.mark.asyncio
async def test_finalize_parked_promote_cancelled_still_promotes_and_clears_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """friction 37491: cancel during promote retries after uncancel and clears the mark."""
    from services.git_integration_worker.cursor_sdk_closeout.park_finalize import (
        finalize_parked,
    )
    from services.git_integration_worker.cursor_sdk_park_for_restart import park_mark

    req = _req(
        dispatch_id="promote-cancel-park",
        execution_id="exec-promote-cancel-p",
        thread_id="9013",
    )
    _admit_running_conductor(req, tmp_path)
    mark = _park_mark(req.dispatch_id, req.thread_id)
    _stub_park_finalize_preamble(monkeypatch)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize."
        "merge_conductor_closeout_hop_authority",
        lambda **_kw: None,
    )
    entered = asyncio.Event()
    promote_calls: list[int] = []
    real_promote = route_mod._mark_terminal_and_promote

    async def _promote_then_real(**kw: Any) -> None:
        promote_calls.append(len(promote_calls) + 1)
        if len(promote_calls) == 1:
            entered.set()
            await asyncio.sleep(60)
        await real_promote(**kw)

    monkeypatch.setattr(route_mod, "_mark_terminal_and_promote", _promote_then_real)
    bus = MagicMock()
    bus.reply = AsyncMock(
        return_value=MagicMock(status_code=201, body={"turn_number": 3})
    )

    task = asyncio.create_task(
        finalize_parked(
            req=req,
            source_repo=tmp_path / "repo",
            bus=bus,
            reply_to="dispatch",
            controller=_controller(),
            mark=mark,
            outcome=_outcome(""),
            exc=None,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    await task
    assert promote_calls == [1, 2]
    assert park_mark(req.dispatch_id) is None
    assert _status(req.dispatch_id) == "cancelled"
