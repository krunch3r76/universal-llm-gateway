"""G3 conductor successor without operator re-admit (FORK-1 / FORK-2)."""

from __future__ import annotations

import json
from contextlib import ExitStack
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


_CLEAR_SNAP = {
    "observed_at": "2026-10-03T23:57:35+00:00",
    "rows": [
        {
            "execution_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "parent_thread": "not-this-lane",
            "status": "completed",
            "stream_state": "completed",
            "purpose": "review",
        }
    ],
}

_NEST_EXEC = "fb4c3f9b-ff78-4cb9-84eb-fa713765640e"
_CDP_EXEC = "4858cfb2-c6e9-4157-ba43-f12f3584685d"


class _PromoteCtl:
    worker_id = "w"

    def is_draining(self) -> bool:
        return False

    def maybe_activate_armed_drain(self, *, dispatch_id: str) -> None:
        return None


def _miss_closeout(execution_id: str) -> str:
    return (
        "status: partial\n\n"
        "The nested implement is still running, so this dispatch does not hop.\n\n"
        f"NEXT_ADMIT: harvest `{execution_id}` on thread 14985, "
        "then re-review only after that repair is on the lane tip.\n"
    )


def _arm_terminal_parent(
    ledger: CursorDispatchLedger,
    *,
    parent_id: str,
    nest_id: str,
    nest_execution_id: str,
    closeout: str,
    admitted_via: str | None = None,
) -> None:
    _admit(
        ledger,
        _req(dispatch_id=parent_id, execution_id=f"exec-{parent_id}"),
        contract="conductor",
    )
    _admit(
        ledger,
        _req(dispatch_id=nest_id, execution_id=nest_execution_id),
        nest_under=parent_id,
    )
    patch = {
        "closeout_body": closeout,
        "closeout_stop_tokens": [],
        "summoning_thread_id": "14901",
        "closeout_harvest_owed": True,
    }
    if admitted_via:
        patch["admitted_via"] = admitted_via
    ledger.merge_record_json(dispatch_id=parent_id, patch=patch)
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=?, park_child_dispatch_id=? "
            "WHERE dispatch_id=?",
            ("completed", nest_id, parent_id),
        )


def _promote_contexts(stack: ExitStack, post: AsyncMock) -> None:
    stack.enter_context(
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            post,
        )
    )
    stack.enter_context(
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
            return_value=_CLEAR_SNAP,
        )
    )
    stack.enter_context(
        patch(
            "services.git_integration_worker.cursor_sdk_park.force_release_sdk_dispatch_slot",
            new=AsyncMock(),
        )
    )
    stack.enter_context(
        patch(
            "services.git_integration_worker.cursor_sdk_park.transfer_sdk_dispatch_slot",
            new=AsyncMock(),
        )
    )
    stack.enter_context(
        patch(
            "services.git_integration_worker.routes.cursor_sdk.maybe_prune_worktree_on_terminal",
            return_value=None,
        )
    )


async def _promote_nest(nest_id: str) -> None:
    from services.git_integration_worker.routes.cursor_sdk import (
        _mark_terminal_and_promote,
    )

    await _mark_terminal_and_promote(
        dispatch_id=nest_id,
        terminal_status="completed",
        controller=_PromoteCtl(),
        emit_tag="TEST",
    )


@pytest.mark.asyncio
async def test_miss1_terminal_parent_nest_close_admits_one_successor() -> None:
    """Path B: NEXT_ADMIT names the nest execution id; nest terminal admits once."""
    ledger = CursorDispatchLedger.instance()
    parent_id = "a2e5ae12e508-09ef511f"
    nest_id = "da610c997d3a-4bb7eb6e"
    _arm_terminal_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=_NEST_EXEC,
        closeout=_miss_closeout(_NEST_EXEC),
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "succ-miss-1"}))
    with ExitStack() as stack:
        _promote_contexts(stack, post)
        await _promote_nest(nest_id)
    assert post.await_count == 1
    assert post.await_args.args[0]["hop_from"] == parent_id


@pytest.mark.asyncio
async def test_miss2_park_resume_parent_nest_close_admits_one_successor() -> None:
    """Path B sees a -r1 giw_park_resume parent the same way as a normal row."""
    ledger = CursorDispatchLedger.instance()
    parent_id = "fc9a4a117472-bb2fdfc0-r1"
    nest_id = "56a6745e4fa0-683ce0c0"
    nest_exec = "0bd55e3c-1f0b-4fe0-87ad-491100d8ce0b"
    _arm_terminal_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=nest_exec,
        closeout=(
            "status: partial\n\n"
            f"NEXT_ADMIT: harvest `{nest_exec}`. "
            "Do not dispatch a second repair.\n"
        ),
        admitted_via="giw_park_resume",
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "succ-miss-2"}))
    with ExitStack() as stack:
        _promote_contexts(stack, post)
        await _promote_nest(nest_id)
    assert post.await_count == 1
    assert post.await_args.args[0]["hop_from"] == parent_id


@pytest.mark.asyncio
async def test_missed_refire_deferred_sweep_admits_once_nest_terminal() -> None:
    """Path C: nest-close re-fire never ran; the sweep admits after the nest is terminal."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        maybe_fire_conductor_hop_reactor,
        release_deferred_conductor_hops,
    )

    ledger = CursorDispatchLedger.instance()
    parent_id = "a2e5ae12e508-09ef511f"
    nest_id = "da610c997d3a-4bb7eb6e"
    _arm_terminal_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=_NEST_EXEC,
        closeout=_miss_closeout(_NEST_EXEC),
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "succ-sweep"}))
    with ExitStack() as stack:
        _promote_contexts(stack, post)
        await maybe_fire_conductor_hop_reactor(dispatch_id=parent_id)
        assert post.await_count == 0
        ledger.mark_terminal(dispatch_id=nest_id, terminal_status="completed")
        admitted = await release_deferred_conductor_hops()
    assert admitted == 1
    assert post.await_count == 1


@pytest.mark.asyncio
async def test_cdp_harvest_still_in_flight_blocks_after_nest_terminal() -> None:
    """NEXT_ADMIT names a live CDP harvest, not the terminal nest — no successor."""
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-cdp-live"
    nest_id = "nest-cdp-live"
    _arm_terminal_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=_NEST_EXEC,
        closeout=_miss_closeout(_CDP_EXEC),
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "should-not"}))
    with ExitStack() as stack:
        _promote_contexts(stack, post)
        stack.enter_context(
            patch(
                "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._collect_execution_targets",
                return_value=[(_CDP_EXEC, False)],
            )
        )
        await _promote_nest(nest_id)
    assert post.await_count == 0


@pytest.mark.asyncio
async def test_hit_parked_waiting_parent_restores_without_successor() -> None:
    """Path A: parked_waiting parent is restored; the nest close does not also hop."""
    ledger = CursorDispatchLedger.instance()
    parent_id = "12097d4213c2-d349f9bd"
    nest_id = "d7a5d4091953-8a9f0797"
    _admit(
        ledger,
        _req(dispatch_id=parent_id, execution_id="exec-hit-parent"),
        contract="conductor",
    )
    _admit(
        ledger,
        _req(dispatch_id=nest_id, execution_id="a64011ec-0d0c-44e3-aa9a-879f639debf8"),
        nest_under=parent_id,
    )
    ledger.merge_record_json(
        dispatch_id=parent_id,
        patch={
            "closeout_body": (
                "Nested child is live. Next admit harvests "
                "`a64011ec-0d0c-44e3-aa9a-879f639debf8`.\n"
            ),
            "closeout_stop_tokens": [],
            "summoning_thread_id": "14901",
        },
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=?, park_child_dispatch_id=? "
            "WHERE dispatch_id=?",
            ("parked_waiting", nest_id, parent_id),
        )
    post = AsyncMock(return_value=(True, {"dispatch_id": "should-not-hit"}))
    with ExitStack() as stack:
        _promote_contexts(stack, post)
        await _promote_nest(nest_id)
    assert post.await_count == 0
    with ledger._connect() as conn:
        parent = conn.execute(
            "SELECT status, park_child_dispatch_id FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    assert parent["status"] == "running"
    assert parent["park_child_dispatch_id"] is None


@pytest.mark.asyncio
async def test_two_nest_terminal_events_admit_one_successor() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-idem"
    nest_id = "nest-idem"
    _arm_terminal_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=_NEST_EXEC,
        closeout=_miss_closeout(_NEST_EXEC),
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "succ-once"}))
    with ExitStack() as stack:
        _promote_contexts(stack, post)
        await _promote_nest(nest_id)
        await _promote_nest(nest_id)
    assert post.await_count == 1


@pytest.mark.asyncio
async def test_live_grandchild_blocks_successor() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-grand"
    nest_id = "nest-grand"
    _arm_terminal_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=_NEST_EXEC,
        closeout=_miss_closeout(_NEST_EXEC),
    )
    _admit(
        ledger,
        _req(dispatch_id="leaf-grand", execution_id="exec-leaf-grand"),
        nest_under=nest_id,
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=? WHERE dispatch_id=?",
            ("running", "leaf-grand"),
        )
    post = AsyncMock(return_value=(True, {"dispatch_id": "should-not-grand"}))
    with ExitStack() as stack:
        _promote_contexts(stack, post)
        await _promote_nest(nest_id)
    assert post.await_count == 0


@pytest.mark.asyncio
async def test_non_conductor_parent_reactor_noop() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-impl"
    nest_id = "nest-impl"
    _admit(
        ledger,
        _req(dispatch_id=parent_id, execution_id="exec-parent-impl"),
        contract="implement",
    )
    _admit(
        ledger,
        _req(dispatch_id=nest_id, execution_id=_NEST_EXEC),
        nest_under=parent_id,
    )
    ledger.merge_record_json(
        dispatch_id=parent_id,
        patch={"closeout_body": _miss_closeout(_NEST_EXEC), "closeout_stop_tokens": []},
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=?, park_child_dispatch_id=? "
            "WHERE dispatch_id=?",
            ("completed", nest_id, parent_id),
        )
    post = AsyncMock(return_value=(True, {"dispatch_id": "should-not-impl"}))
    with ExitStack() as stack:
        _promote_contexts(stack, post)
        await _promote_nest(nest_id)
    assert post.await_count == 0


@pytest.mark.asyncio
async def test_prose_parked_transport_blocks_after_nest_terminal() -> None:
    """A hold token in the closeout prose still blocks when the nest is terminal."""
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-prose-hold"
    nest_id = "nest-prose-hold"
    closeout = (
        "status: partial\n\n"
        f"NEXT_ADMIT: harvest `{_NEST_EXEC}`.\n\n"
        "Hold this admit. stop: PARKED_TRANSPORT until the transport returns.\n"
    )
    _arm_terminal_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=_NEST_EXEC,
        closeout=closeout,
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "should-not-prose"}))
    with ExitStack() as stack:
        _promote_contexts(stack, post)
        await _promote_nest(nest_id)
    assert post.await_count == 0


@pytest.mark.asyncio
async def test_confirm_pending_footer_blocks_after_nest_terminal() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-confirm"
    nest_id = "nest-confirm"
    _arm_terminal_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=_NEST_EXEC,
        closeout=_miss_closeout(_NEST_EXEC),
    )
    ledger.merge_record_json(
        dispatch_id=parent_id,
        patch={"closeout_stop_tokens": ["CONFIRM_PENDING"]},
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "should-not-confirm"}))
    with ExitStack() as stack:
        _promote_contexts(stack, post)
        await _promote_nest(nest_id)
    assert post.await_count == 0


@pytest.mark.asyncio
async def test_token_also_in_cdp_registry_does_not_lift() -> None:
    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-cdp-ambig"
    nest_id = "nest-cdp-ambig"
    _arm_terminal_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=_NEST_EXEC,
        closeout=_miss_closeout(_NEST_EXEC),
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "should-not-ambig"}))
    with ExitStack() as stack:
        _promote_contexts(stack, post)
        stack.enter_context(
            patch(
                "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._collect_execution_targets",
                return_value=[("other-cdp-exec", False)],
            )
        )
        await _promote_nest(nest_id)
    assert post.await_count == 0


@pytest.mark.asyncio
async def test_park_resumed_nest_lineage_head_admits_on_sweep() -> None:
    """Shared execution id: the -r1 head counts, the cancelled original does not."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        maybe_fire_conductor_hop_reactor,
        release_deferred_conductor_hops,
    )

    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-park-lineage"
    nest_id = "nest-park-lineage"
    resumed_id = "nest-park-lineage-r1"
    _arm_terminal_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=_NEST_EXEC,
        closeout=_miss_closeout(_NEST_EXEC),
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "succ-park-lineage"}))
    with ExitStack() as stack:
        _promote_contexts(stack, post)
        await maybe_fire_conductor_hop_reactor(dispatch_id=parent_id)
        assert post.await_count == 0
        with ledger._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET status=?, park_kind=?, "
                "park_resumed_by=? WHERE dispatch_id=?",
                ("cancelled", "park_for_restart", resumed_id, nest_id),
            )
        _admit(
            ledger,
            _req(
                dispatch_id=resumed_id,
                execution_id=_NEST_EXEC,
                resume_of=nest_id,
            ),
        )
        ledger.mark_terminal(dispatch_id=resumed_id, terminal_status="completed")
        admitted = await release_deferred_conductor_hops()
    assert admitted == 1
    assert post.await_count == 1


@pytest.mark.asyncio
async def test_open_park_without_resume_does_not_admit() -> None:
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        maybe_fire_conductor_hop_reactor,
        release_deferred_conductor_hops,
    )

    ledger = CursorDispatchLedger.instance()
    parent_id = "parent-open-park"
    nest_id = "nest-open-park"
    _arm_terminal_parent(
        ledger,
        parent_id=parent_id,
        nest_id=nest_id,
        nest_execution_id=_NEST_EXEC,
        closeout=_miss_closeout(_NEST_EXEC),
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "should-not-open-park"}))
    with ExitStack() as stack:
        _promote_contexts(stack, post)
        await maybe_fire_conductor_hop_reactor(dispatch_id=parent_id)
        with ledger._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET status=?, park_kind=? "
                "WHERE dispatch_id=?",
                ("cancelled", "park_for_restart", nest_id),
            )
        admitted = await release_deferred_conductor_hops()
    assert admitted == 0
    assert post.await_count == 0


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
