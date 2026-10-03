"""friction 37490: pre-promote awaits must not skip the terminal mark."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.routes import cursor_sdk as route_mod
from services.git_integration_worker.tests.test_conductor_closeout_terminate_order import (
    _admit_running_conductor,
    _controller,
    _install_closeout_stubs,
    _outcome,
    _req,
)
from services.git_integration_worker.tests.test_conductor_merge_raise_still_terminal import (
    _status,
)

pytestmark = pytest.mark.offline

_PF = "services.git_integration_worker.cursor_sdk_closeout.park_finalize"


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(tmp_path / "homes"))
    CursorDispatchLedger._instance = None
    from services.git_integration_worker.cursor_sdk_events import (
        reset_terminal_emitted_registry,
    )

    reset_terminal_emitted_registry()
    yield
    CursorDispatchLedger._instance = None


def _counting_raise(calls: list[str], label: str):
    def _raise(*_a: Any, **_k: Any) -> None:
        calls.append(label)
        raise RuntimeError(f"{label} boom")

    return _raise


def _async_counting_raise(calls: list[str], label: str):
    async def _raise(*_a: Any, **_k: Any) -> None:
        calls.append(label)
        raise RuntimeError(f"{label} boom")

    return _raise


def _install_park_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_PF}.emit_sdk_park_parked", lambda **_kw: None)
    monkeypatch.setattr(f"{_PF}.terminal_emitted", lambda *_a, **_k: True)
    monkeypatch.setattr(f"{_PF}.clear_park_mark", lambda *_a, **_k: None)
    monkeypatch.setattr(route_mod, "_promote_queued_for_lease", AsyncMock())
    monkeypatch.setattr(route_mod, "maybe_prune_worktree_on_terminal", lambda **_kw: None)
    monkeypatch.setattr(route_mod, "_terminate_link", AsyncMock())


@pytest.mark.asyncio
async def test_deliver_trigger_raise_still_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = _req(dispatch_id="trigger-raise-deliver", execution_id="exec-trigger-raise-d")
    _admit_running_conductor(req, tmp_path)
    call_order: list[str] = []
    _install_closeout_stubs(
        monkeypatch, closeout_body="status: complete\n", call_order=call_order
    )
    raise_calls: list[str] = []
    monkeypatch.setattr(
        route_mod,
        "emit_implement_closeout_trigger",
        _async_counting_raise(raise_calls, "trigger-raise"),
    )
    monkeypatch.setattr(route_mod, "_terminate_link", AsyncMock())
    bus = MagicMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=201, body={"turn_number": 2}))

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

    assert raise_calls == ["trigger-raise"]
    assert "promote" in call_order
    assert _status(req.dispatch_id) == "completed"


@pytest.mark.asyncio
async def test_finalize_parked_harvest_raise_still_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.cursor_sdk_closeout.park_finalize import (
        finalize_parked,
    )
    from services.git_integration_worker.cursor_sdk_park_for_restart import ParkMark

    req = _req(dispatch_id="harvest-raise-park", execution_id="exec-harvest-raise-p", thread_id="9012")
    _admit_running_conductor(req, tmp_path)
    mark = ParkMark(
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        intent_id="intent-37490",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="2026-10-01T00:00:00Z",
        method="run_cancel",
        marked_at=0.0,
    )
    raise_calls: list[str] = []
    monkeypatch.setattr(
        f"{_PF}.emit_partial_harvest_on_park",
        _counting_raise(raise_calls, "harvest-raise"),
    )
    monkeypatch.setattr(f"{_PF}.mark_parked", lambda **_kw: None)
    _install_park_stubs(monkeypatch)
    bus = MagicMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=201, body={"turn_number": 3}))

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

    assert raise_calls == ["harvest-raise"]
    assert _status(req.dispatch_id) == "cancelled"


@pytest.mark.asyncio
async def test_finalize_parked_mark_parked_raise_still_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.cursor_sdk_closeout.park_finalize import (
        finalize_parked,
    )
    from services.git_integration_worker.cursor_sdk_park_for_restart import ParkMark

    req = _req(
        dispatch_id="mark-parked-raise-park",
        execution_id="exec-mark-parked-raise-p",
        thread_id="9013",
    )
    _admit_running_conductor(req, tmp_path)
    mark = ParkMark(
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        intent_id="intent-37490",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="2026-10-01T00:00:00Z",
        method="run_cancel",
        marked_at=0.0,
    )
    raise_calls: list[str] = []
    monkeypatch.setattr(f"{_PF}.emit_partial_harvest_on_park", lambda *_a, **_k: {})
    monkeypatch.setattr(
        f"{_PF}.mark_parked",
        _counting_raise(raise_calls, "mark-parked-raise"),
    )
    _install_park_stubs(monkeypatch)
    bus = MagicMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=201, body={"turn_number": 3}))

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

    assert raise_calls == ["mark-parked-raise"]
    assert _status(req.dispatch_id) == "cancelled"


@pytest.mark.asyncio
async def test_finalize_parked_bus_reply_raise_still_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.cursor_sdk_closeout.park_finalize import (
        finalize_parked,
    )
    from services.git_integration_worker.cursor_sdk_park_for_restart import ParkMark

    req = _req(
        dispatch_id="bus-reply-raise-park",
        execution_id="exec-bus-reply-raise-p",
        thread_id="9014",
    )
    _admit_running_conductor(req, tmp_path)
    mark = ParkMark(
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        intent_id="intent-37490",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="2026-10-01T00:00:00Z",
        method="run_cancel",
        marked_at=0.0,
    )
    raise_calls: list[str] = []
    monkeypatch.setattr(f"{_PF}.emit_partial_harvest_on_park", lambda *_a, **_k: {})
    monkeypatch.setattr(f"{_PF}.mark_parked", lambda **_kw: None)
    _install_park_stubs(monkeypatch)
    bus = MagicMock()
    bus.reply = _async_counting_raise(raise_calls, "bus-reply-raise")

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

    assert raise_calls == ["bus-reply-raise"]
    assert _status(req.dispatch_id) == "cancelled"
