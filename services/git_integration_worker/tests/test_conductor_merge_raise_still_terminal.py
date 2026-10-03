"""friction 37404: hop-authority merge raise must not skip the terminal mark."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout import conductor_hop as hop_mod
from services.git_integration_worker.routes import cursor_sdk as route_mod
from services.git_integration_worker.tests.test_conductor_closeout_terminate_order import (
    _admit_running_conductor,
    _controller,
    _install_closeout_stubs,
    _outcome,
    _req,
)

pytestmark = pytest.mark.offline


def test_public_merge_swallows_inner_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """A third caller of the public name needs no call-site try/except."""
    raise_calls: list[str] = []
    monkeypatch.setattr(
        hop_mod,
        "_merge_conductor_closeout_hop_authority",
        _counting_raise(raise_calls),
    )
    hop_mod.merge_conductor_closeout_hop_authority(
        dispatch_id="merge-raise-direct",
        closeout_body="",
        thread_id="1",
    )
    assert raise_calls == ["merge-raise"]


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
        hop_mod,
        "_merge_conductor_closeout_hop_authority",
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
        hop_mod,
        "_merge_conductor_closeout_hop_authority",
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
