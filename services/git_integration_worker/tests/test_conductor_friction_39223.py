"""friction:39223 — nested-child park lift and id-matched park-harvest continue."""

from __future__ import annotations

import inspect
import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from agent_bus_store.body_auto_spill import AUTO_OVERFLOW_BRIEFING
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    SKIP_GATE_NEXT_ADMIT_BLOCKED,
    maybe_fire_conductor_hop_reactor,
    release_deferred_conductor_hops,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog import (
    maybe_fire_conductor_hop_watchdog,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (
    _harvest_id_boundary_match,
    _reply_matches_harvest_execution,
    park_harvest_continue_owed,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_SHAPE_B_FULL = "994dc2b4-d1dd-4206-bd97-2ad0109478bc"
_SHAPE_B_OTHER = "f01914a3-015c-4449-be62-1ac2911bf02b"
_NEST_EXEC = "fb4c3f9b-ff78-4cb9-84eb-fa713765640e"


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    dispatch_id = str(overrides.get("dispatch_id", "parent-39223"))
    base = {
        "thread_id": "16692",
        "model": "cursor/composer-2.5",
        "dispatch_id": dispatch_id,
        "execution_id": str(overrides.get("execution_id", f"exec-{dispatch_id}")),
        "message": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    contract: str = "conductor",
    nest_under: str | None = None,
    work_key: str | None = None,
) -> None:
    wk = work_key or f"todo:39223-{req.dispatch_id}"
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


def _parked_parent(
    ledger: CursorDispatchLedger,
    *,
    parent_id: str,
    nest_id: str,
    nest_execution_id: str,
    closeout_tokens: list[str],
    closeout_turn: int = 62,
    child_status: str = "running",
    park_kind: str | None = None,
) -> None:
    _admit(
        ledger,
        _req(dispatch_id=parent_id, execution_id=f"exec-{parent_id}"),
        contract="conductor",
    )
    nest_wk = f"todo:39223-{nest_id}"
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, thread_id, status, fingerprint, execution_id, "
            "caller_agent, resolved_model, contract, source_repo, lease_key, "
            "work_key, source_ref, nest_under, record_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                nest_id,
                "16692",
                child_status,
                f"fp-{nest_id}",
                nest_execution_id,
                "cursor",
                "composer-2.5",
                "implement",
                "/repo",
                "/repo",
                nest_wk,
                nest_wk,
                parent_id,
                json.dumps({"nest_under": parent_id, "contract": "implement", "lane": "B"}),
            ),
        )
    closeout = (
        f"stop: PARKED_TRANSPORT\nNEXT_ADMIT: harvest {nest_execution_id}\n"
    )
    ledger.merge_record_json(
        dispatch_id=parent_id,
        patch={
            "closeout_body": closeout,
            "closeout_stop_tokens": closeout_tokens,
            "closeout_turn": closeout_turn,
            "closeout_harvest_owed": True,
            "summoning_thread_id": "14901",
        },
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=?, park_child_dispatch_id=? "
            "WHERE dispatch_id=?",
            ("completed", nest_id, parent_id),
        )
        if child_status != "running":
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET status=? WHERE dispatch_id=?",
                (child_status, nest_id),
            )
        if park_kind:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET park_kind=? WHERE dispatch_id=?",
                (park_kind, nest_id),
            )


def _thread_turns(turns: list[dict]) -> patch:
    return patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest._fetch_thread_turns",
        side_effect=lambda _tid: list(turns),
    )


def _no_registry_watermark() -> patch:
    return patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._harvest_execution_started_at_iso",
        return_value=None,
    )


@pytest.mark.asyncio
async def test_shape_a_restart_live_child_stamps_then_sweep_admits() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-term"
    nest_id = "child-1"
    _parked_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=_NEST_EXEC,
        closeout_tokens=["PARKED_TRANSPORT"],
        child_status="running",
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "succ-nest-live"}))
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
                post,
            )
        )
        stack.enter_context(
            patch(
                "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
                return_value={"observed_at": "2026-10-13T00:00:00+00:00", "rows": []},
            )
        )
        await maybe_fire_conductor_hop_reactor(dispatch_id=parent_id)
        post.assert_not_awaited()
        with ledger._connect() as conn:
            rec = json.loads(
                conn.execute(
                    "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                    (parent_id,),
                ).fetchone()["record_json"]
            )
        assert rec.get("hop_deferral_gate") == SKIP_GATE_NEXT_ADMIT_BLOCKED
        assert rec.get("hop_park_harvest_fired_at") is None
        assert await release_deferred_conductor_hops() == 0
        with ledger._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET status=? WHERE dispatch_id=?",
                ("completed", nest_id),
            )
        assert await release_deferred_conductor_hops() == 1
    post.assert_awaited_once()


@pytest.mark.asyncio
async def test_shape_a_both_tokens_terminal_nest_posts_hop() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-both-tok"
    nest_id = "child-both"
    _parked_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=_NEST_EXEC,
        closeout_tokens=["PARKED_TRANSPORT", "CONSULT_PENDING"],
        child_status="completed",
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "succ-both"}))
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        post,
    ), patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value={"observed_at": "2026-10-13T00:00:00+00:00", "rows": []},
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id=parent_id)
    post.assert_awaited_once()


@pytest.mark.asyncio
async def test_shape_a_pre_closeout_reply_hop_not_park_harvest() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-pre-reply"
    nest_id = "child-pre"
    _parked_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=_NEST_EXEC,
        closeout_tokens=["PARKED_TRANSPORT"],
        closeout_turn=77,
        child_status="completed",
    )
    turns = [
        {
            "turn_number": 76,
            "from_agent": "web-anthropic",
            "subject": f"cdp reply — {_NEST_EXEC}",
        },
    ]
    post = AsyncMock(return_value=(True, {"dispatch_id": "succ-pre"}))
    with ExitStack() as stack:
        stack.enter_context(_thread_turns(turns))
        stack.enter_context(
            patch(
                "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
                post,
            )
        )
        stack.enter_context(
            patch(
                "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
                return_value={"observed_at": "2026-10-13T00:00:00+00:00", "rows": []},
            )
        )
        await maybe_fire_conductor_hop_reactor(dispatch_id=parent_id)
        await maybe_fire_conductor_hop_watchdog(dispatch_id=parent_id)
    assert post.await_count == 1
    assert post.await_args_list[0].args[0]["hop_reason"] != "park_harvest"
    with ledger._connect() as conn:
        rec = json.loads(
            conn.execute(
                "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (parent_id,),
            ).fetchone()["record_json"]
        )
    assert rec.get("hop_park_harvest_continued_at") is None


def test_shape_b_wrong_id_then_pre_closeout_match() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-shape-b"
    _admit(ledger, _req(dispatch_id=parent_id), contract="conductor")
    closeout = f"NEXT_ADMIT: harvest {_SHAPE_B_FULL}\n"
    ledger.merge_record_json(
        dispatch_id=parent_id,
        patch={
            "closeout_body": closeout,
            "closeout_stop_tokens": ["PARKED_TRANSPORT"],
            "closeout_turn": 62,
            "closeout_harvest_owed": True,
            "hop_park_harvest_fired_at": 1.0,
        },
    )
    ledger.mark_terminal(dispatch_id=parent_id, terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    mapped = {k: row[k] for k in row.keys()}

    wrong_turn = [
        {
            "turn_number": 70,
            "from_agent": "web-anthropic",
            "body": f"execution_id: `{_SHAPE_B_OTHER}`\nmentions {_SHAPE_B_FULL}",
        },
    ]
    with _thread_turns(wrong_turn), _no_registry_watermark():
        assert not park_harvest_continue_owed(mapped)

    both_turns = wrong_turn + [
        {
            "turn_number": 60,
            "from_agent": "web-anthropic",
            "subject": f"cdp reply — {_SHAPE_B_FULL}",
        },
    ]
    with _thread_turns(both_turns), _no_registry_watermark():
        assert park_harvest_continue_owed(mapped)

    assert _harvest_id_boundary_match(_SHAPE_B_FULL, "994dc2b4")
    assert not _harvest_id_boundary_match("994dc2b4abcd", "994dc2b4")


def test_shape_b_unstamped_harvest_id_never_continues() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-unstamped"
    _admit(ledger, _req(dispatch_id=parent_id), contract="conductor")
    prose = (
        "stop: PARKED_TRANSPORT\n"
        f"prose mentions {_SHAPE_B_OTHER} without NEXT_ADMIT line\n"
    )
    ledger.merge_record_json(
        dispatch_id=parent_id,
        patch={
            "closeout_body": prose,
            "closeout_stop_tokens": ["PARKED_TRANSPORT"],
            "closeout_turn": 40,
            "closeout_harvest_owed": True,
            "hop_park_harvest_fired_at": 1.0,
        },
    )
    ledger.mark_terminal(dispatch_id=parent_id, terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    mapped = {k: row[k] for k in row.keys()}
    turns = [
        {"turn_number": 35, "from_agent": "web-anthropic", "subject": f"cdp reply — {_SHAPE_B_OTHER}"},
        {"turn_number": 45, "from_agent": "web-anthropic", "subject": f"cdp reply — {_SHAPE_B_OTHER}"},
    ]
    with _thread_turns(turns), _no_registry_watermark():
        assert not park_harvest_continue_owed(mapped)


def test_spill_subject_still_matches_overflow_body() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-spill"
    _admit(ledger, _req(dispatch_id=parent_id), contract="conductor")
    ledger.merge_record_json(
        dispatch_id=parent_id,
        patch={
            "closeout_body": f"NEXT_ADMIT: harvest {_SHAPE_B_FULL}\n",
            "closeout_stop_tokens": ["PARKED_TRANSPORT"],
            "closeout_turn": 10,
            "closeout_harvest_owed": True,
            "hop_park_harvest_fired_at": 1.0,
        },
    )
    ledger.mark_terminal(dispatch_id=parent_id, terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    mapped = {k: row[k] for k in row.keys()}
    ok_turns = [
        {
            "turn_number": 11,
            "from_agent": "web-anthropic",
            "subject": "cdp reply — 994dc2b4",
            "body": AUTO_OVERFLOW_BRIEFING,
        },
    ]
    bad_turns = [
        {
            "turn_number": 11,
            "from_agent": "web-anthropic",
            "subject": "CONSULT_REPLY relay",
            "body": f"mentions {_SHAPE_B_FULL}",
        },
    ]
    with _thread_turns(ok_turns), _no_registry_watermark():
        assert _reply_matches_harvest_execution(mapped, _SHAPE_B_FULL)
    with _thread_turns(bad_turns), _no_registry_watermark():
        assert not _reply_matches_harvest_execution(mapped, _SHAPE_B_FULL)


def test_reactor_evaluates_continue_on_worker_thread() -> None:
    source = inspect.getsource(maybe_fire_conductor_hop_reactor)
    assert "asyncio.to_thread" in source
    assert "park_harvest_continue_owed" in source
