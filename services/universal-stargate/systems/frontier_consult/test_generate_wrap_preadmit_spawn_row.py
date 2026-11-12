"""friction:41315 A — the spawn row survives a materialize that dies.

The materialize at ``dispatch_cursor_sdk_generate_route`` is a held executor
call upstream of the GIW admit, so a request that raises or times out inside it
never reaches ``ledger.admit``. These tests pin the pre-materialize row and its
terminal patch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import Response
from spawn_ledger.spawn_endpoint_house_ledger import (
    _fold_spawn_rows,
    _read_rows,
    ledger_path,
    mint_row_id,
    project_row_status,
)

from . import generate_wrap as gw
from .generate_wrap import (
    GenerateWrapResult,
    _fail_pending_spawn_row,
    _record_pending_spawn_row,
    dispatch_cursor_sdk_generate_route,
)
from .route import TeamDispatchGenerateBody


class _Body:
    def __init__(self, **kw: Any) -> None:
        self.dispatch_thread_id = kw.get("dispatch_thread_id", "21723")
        self.caller_agent = kw.get("caller_agent", "cursor-sdk")
        self.dispatch_id = kw.get("dispatch_id")
        self.model = kw.get("model", "cursor/composer-2.5")
        self.work_key = kw.get("work_key", "friction:41315")


@pytest.fixture
def watch_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("spawn_ledger.spawn_endpoint_house_ledger.WATCH_DIR", tmp_path)
    return tmp_path


def _folded(watch_dir: Path) -> dict[str, dict[str, Any]]:
    return _fold_spawn_rows(_read_rows(ledger_path(watch_dir)))


def test_pending_row_is_visible_during_the_hold(watch_dir: Path) -> None:
    """(c) registry/ledger reads must see the dispatch while materialize runs."""
    row_id = mint_row_id()
    assert _record_pending_spawn_row(
        row_id=row_id, body=_Body(), work_key="friction:41315"
    )

    rows = _folded(watch_dir)
    assert list(rows) == [row_id]
    row = rows[row_id]
    assert row["state"] == "launching"
    assert row["work_key"] == "friction:41315"
    assert row["model"] == "cursor/composer-2.5"
    assert row["thread"] == "21723"
    assert row["materialize_started_at"].endswith("Z")


def test_materialize_failure_leaves_one_terminal_row(watch_dir: Path) -> None:
    """(a) materialize raises — one row, failed, error class recorded."""
    row_id = mint_row_id()
    _record_pending_spawn_row(row_id=row_id, body=_Body(), work_key=None)

    _fail_pending_spawn_row(
        row_id=row_id,
        exc=TimeoutError("cortex read"),
        stage="materialize",
        elapsed_ms=20_000,
    )

    rows = _folded(watch_dir)
    assert list(rows) == [row_id], "a dead materialize must not fork a second row"
    row = rows[row_id]
    assert row["state"] == "failed"
    assert row["reason"] == "TimeoutError"
    assert row["materialize_elapsed_ms"] == 20_000


def test_no_work_key_or_model_omits_those_fields(watch_dir: Path) -> None:
    """Partial failure: a body missing work_key/model still records a row."""
    row_id = mint_row_id()
    body = _Body(model="", work_key=None)
    assert _record_pending_spawn_row(row_id=row_id, body=body, work_key=None)

    row = _folded(watch_dir)[row_id]
    assert row["state"] == "launching"
    assert "work_key" not in row
    assert "model" not in row


def test_duplicate_row_id_is_not_claimed_for_patching(watch_dir: Path) -> None:
    """Wrong ordering: a row_id already in the ledger is not ours to patch."""
    row_id = mint_row_id()
    assert _record_pending_spawn_row(row_id=row_id, body=_Body(), work_key=None)
    assert not _record_pending_spawn_row(row_id=row_id, body=_Body(), work_key=None)


def test_failed_row_projects_as_failed_not_unknown(watch_dir: Path) -> None:
    """MED-2: project_row_status reads status rows, never spawn.state."""
    row_id = mint_row_id()
    _record_pending_spawn_row(row_id=row_id, body=_Body(), work_key=None)

    _fail_pending_spawn_row(
        row_id=row_id, exc=RuntimeError("boom"), stage="materialize", elapsed_ms=5
    )

    projected = project_row_status(row_id, watch_dir=watch_dir)
    assert projected["status"] == "failed"
    assert projected["source_status"] == "RuntimeError"
    assert _folded(watch_dir)[row_id]["reason"] == "RuntimeError"


def test_failure_patch_never_replaces_the_original_exception(
    watch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LOW-4: a corrupt ledger must not swallow the exception being raised."""
    row_id = mint_row_id()
    _record_pending_spawn_row(row_id=row_id, body=_Body(), work_key=None)

    def _boom(*_a: Any, **_kw: Any) -> None:
        raise UnicodeDecodeError("utf-8", b"", 0, 1, "corrupt ledger")

    monkeypatch.setattr(
        "spawn_ledger.spawn_endpoint_house_ledger.patch_spawn_row", _boom
    )

    # Returns normally; the caller's `raise` is what propagates.
    _fail_pending_spawn_row(
        row_id=row_id, exc=TimeoutError("held"), stage="materialize", elapsed_ms=1
    )


def test_stage_records_which_step_died(watch_dir: Path) -> None:
    """HIGH-1: a post-materialize death is distinguishable from a materialize one."""
    row_id = mint_row_id()
    _record_pending_spawn_row(row_id=row_id, body=_Body(), work_key=None)

    _fail_pending_spawn_row(row_id=row_id, exc=None, stage="spawn_spine", elapsed_ms=7)

    row = _folded(watch_dir)[row_id]
    assert row["state"] == "failed"
    assert row["stage"] == "spawn_spine"
    # No exception in hand (an early return): the stage stands in as the reason.
    assert row["reason"] == "spawn_spine"


# --- route-level (MED-3) -------------------------------------------------


def _conductor_body() -> TeamDispatchGenerateBody:
    return TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="21723",
        job="conductor",
        lane="B",
        source_ref="todo:friction-41315",
        caller_agent="cursor-sdk",
        work_key="friction:41315",
    )


@pytest.fixture
def route_harness(watch_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the route's collaborators so only the spawn-row path is exercised."""
    from unittest.mock import AsyncMock

    sdk_mock = AsyncMock(
        return_value={
            "execution_id": "e1",
            "dispatch_id": "disp-preadmit-e1",
            "thread_id": "21723",
        }
    )
    monkeypatch.setattr(gw, "dispatch_cursor_sdk_generate", sdk_mock)
    monkeypatch.setattr(gw, "probe_thread", AsyncMock(return_value={"turn_count": 1}))
    monkeypatch.setattr(
        gw,
        "refuse_conductor_operator_lane_summon",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        gw,
        "resolve_cursor_sdk_thread_targets",
        AsyncMock(return_value=("21723", None, False)),
    )
    return {"sdk": sdk_mock}


def _spawn_rows(watch_dir: Path) -> list[dict[str, Any]]:
    return list(_folded(watch_dir).values())


@pytest.mark.asyncio
async def test_route_materialize_runtime_error_leaves_one_failed_row(
    watch_dir: Path, route_harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a) materialize raises — one failed row, the exception propagates."""

    def _boom(**_kw: Any) -> GenerateWrapResult:
        raise RuntimeError("cortex read died")

    monkeypatch.setattr(gw, "prepare_conductor_packet", _boom)

    with pytest.raises(RuntimeError):
        await dispatch_cursor_sdk_generate_route(
            request_id="r1",
            body=_conductor_body(),
            seat="cursor-sdk",
            response=Response(),
        )

    rows = _spawn_rows(watch_dir)
    assert len(rows) == 1
    assert rows[0]["state"] == "failed"
    assert rows[0]["reason"] == "RuntimeError"
    assert rows[0]["stage"] == "materialize"


@pytest.mark.asyncio
async def test_route_materialize_cancelled_leaves_one_failed_row(
    watch_dir: Path, route_harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client cut mid-materialize: CancelledError is a BaseException, still terminal."""
    import asyncio as _asyncio

    def _cut(**_kw: Any) -> GenerateWrapResult:
        raise _asyncio.CancelledError()

    monkeypatch.setattr(gw, "prepare_conductor_packet", _cut)

    with pytest.raises(_asyncio.CancelledError):
        await dispatch_cursor_sdk_generate_route(
            request_id="r2",
            body=_conductor_body(),
            seat="cursor-sdk",
            response=Response(),
        )

    rows = _spawn_rows(watch_dir)
    assert len(rows) == 1
    assert rows[0]["state"] == "failed"
    assert rows[0]["reason"] == "CancelledError"


@pytest.mark.asyncio
async def test_route_success_leaves_one_live_row(
    watch_dir: Path, route_harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b) materialize succeeds — exactly one row, promoted to live, not duplicated."""
    monkeypatch.setattr(
        gw,
        "prepare_conductor_packet",
        lambda **_kw: GenerateWrapResult(
            packet_path=None, packet_markdown="# packet", materialized=True
        ),
    )

    await dispatch_cursor_sdk_generate_route(
        request_id="r3",
        body=_conductor_body(),
        seat="cursor-sdk",
        response=Response(),
    )

    rows = _spawn_rows(watch_dir)
    assert len(rows) == 1, "promotion must patch the pre-materialize row, not insert"
    assert rows[0]["state"] == "live"
    assert "reason" not in rows[0]


@pytest.mark.asyncio
async def test_route_thread_target_refusal_after_materialize_is_not_left_launching(
    watch_dir: Path, route_harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """HIGH-1: a refusal between materialize and promotion must not strand the row.

    This is the regression test for the stale-``launching`` orphan: at
    1bf993f25 the try covered only the materialize await, so this exit left
    state=launching and ingest_report_turn's thread fallback could attach a
    later report turn to it.
    """
    from .admission import FrontierEndpointError

    monkeypatch.setattr(
        gw,
        "prepare_conductor_packet",
        lambda **_kw: GenerateWrapResult(
            packet_path=None, packet_markdown="# packet", materialized=True
        ),
    )

    def _refuse(**_kw: Any) -> None:
        raise FrontierEndpointError(
            request_id="r4",
            field="reuse_thread",
            reason="thread target refused",
            status_code=422,
            code="thread_target_refused",
        )

    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        gw, "resolve_cursor_sdk_thread_targets", AsyncMock(side_effect=_refuse)
    )

    # The route converts this refusal into a 422 response rather than letting
    # it propagate, which is precisely why the guard is `finally` and not
    # `except`: an early return strands the row just as an exception does.
    result = await dispatch_cursor_sdk_generate_route(
        request_id="r4",
        body=_conductor_body(),
        seat="cursor-sdk",
        response=Response(),
    )
    assert getattr(result, "status_code", None) == 422

    rows = _spawn_rows(watch_dir)
    assert len(rows) == 1
    assert rows[0]["state"] == "failed", "stale launching row (HIGH-1)"
    assert rows[0]["stage"] == "thread_targets"
    assert (
        project_row_status(rows[0]["row_id"], watch_dir=watch_dir)["status"] == "failed"
    )


@pytest.mark.asyncio
async def test_route_spawn_spine_failure_returns_500_and_marks_row(
    watch_dir: Path, route_harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial failure: the 500 early return is an exit, so finally must fire."""
    monkeypatch.setattr(
        gw,
        "prepare_conductor_packet",
        lambda **_kw: GenerateWrapResult(
            packet_path=None, packet_markdown="# packet", materialized=True
        ),
    )
    monkeypatch.setattr(
        "spawn_ledger.spawn_endpoint_house_ledger.patch_spawn_row",
        lambda _rid, **_kw: {"ok": False, "error": "row_not_found"},
    )

    result = await dispatch_cursor_sdk_generate_route(
        request_id="r5",
        body=_conductor_body(),
        seat="cursor-sdk",
        response=Response(),
    )

    assert getattr(result, "status_code", None) == 500
