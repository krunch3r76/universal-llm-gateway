"""Parallel Auto admits for ``isolated_lane_conductor`` (G5 F9–F16, F7)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.execution_mode import (
    ISOLATED_LANE_CONDUCTOR_MODE,
    LEASE_FREE_PROPAGATE_MODE,
    declared_execution_mode,
    resolve_execution_mode_at_enqueue,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.liveness import queue_admission_health
from services.git_integration_worker.cursor_auto.queue import (
    AutoJob,
    AutoJobQueue,
    get_queue,
    reset_queue_for_tests,
)
from services.git_integration_worker.cursor_auto.queue_health_events import (
    reset_rising_edge_state_for_tests,
)


@pytest.fixture(autouse=True)
def _isolated_auto_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    AutoJobLedger.reset_for_tests()
    reset_queue_for_tests(durable=True)
    reset_rising_edge_state_for_tests()
    yield
    AutoJobLedger.reset_for_tests()
    reset_rising_edge_state_for_tests()


def _enqueue_conductor(
    queue: AutoJobQueue,
    *,
    work_key: str | None = "todo:x",
    thread_id: str = "t1",
    turn: int = 1,
    contract: str = "investigate",
    lane: str = "B",
) -> AutoJob:
    mode = resolve_execution_mode_at_enqueue(
        contract=contract,
        lane=lane,
        work_key=work_key,
    )
    return queue.enqueue(
        thread_id=thread_id,
        turn_number=turn,
        subject=f"turn {turn}",
        body="TYPE: DIRECTIVE\nvision: test\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract=contract,
        lane=lane,
        work_key=work_key,
        execution_mode=mode.mode,
        execution_mode_declare_reason=mode.reason,
    )


def test_f1_explicit_work_key_lane_b_investigate_concurrent() -> None:
    assert (
        declared_execution_mode(
            contract="investigate",
            lane="B",
            work_key="todo:x",
        )
        == ISOLATED_LANE_CONDUCTOR_MODE
    )


def test_f2_whitespace_work_key_collapses_to_one_claim() -> None:
    queue = get_queue()
    j1 = _enqueue_conductor(queue, thread_id="a", turn=1, work_key=" todo:x ")
    j2 = _enqueue_conductor(queue, thread_id="b", turn=1, work_key="todo:x")
    assert j1.work_key == "todo:x"
    assert j2.work_key == "todo:x"
    assert queue.claim_next_concurrent() is not None
    assert queue.claim_next_concurrent() is None
    assert queue.get(j2.job_id).status == "queued"


def test_f2_same_work_key_only_one_concurrent_claim() -> None:
    queue = get_queue()
    j1 = _enqueue_conductor(queue, thread_id="a", turn=1)
    j2 = _enqueue_conductor(queue, thread_id="b", turn=1)
    c1 = queue.claim_next_concurrent()
    assert c1 is not None
    assert c1.job_id == j1.job_id
    c2 = queue.claim_next_concurrent()
    assert c2 is None
    assert queue.get(j2.job_id).status == "queued"


def test_f4_raw_isolated_without_predicate_serial_and_decline_reason() -> None:
    res = resolve_execution_mode_at_enqueue(
        contract="investigate",
        requested=ISOLATED_LANE_CONDUCTOR_MODE,
        lane=None,
        work_key=None,
    )
    assert res.mode == "serial"
    assert res.reason == "predicate_unmet_requested_declined"


def test_f9_positive_concurrent_claim_persists_mode_and_work_key() -> None:
    queue = get_queue()
    _enqueue_conductor(queue)
    claimed = queue.claim_next_concurrent()
    assert claimed is not None
    assert claimed.execution_mode == ISOLATED_LANE_CONDUCTOR_MODE
    assert claimed.work_key == "todo:x"


def test_f10_ledger_round_trip_after_restart() -> None:
    queue = get_queue()
    job = _enqueue_conductor(queue)
    reset_queue_for_tests(durable=True)
    rows = get_ledger().list_open()
    match = [r for r in rows if r.job_id == job.job_id]
    assert len(match) == 1
    assert match[0].execution_mode == ISOLATED_LANE_CONDUCTOR_MODE
    assert match[0].work_key == "todo:x"


def test_f11_keyless_concurrent_jobs_both_claimable() -> None:
    queue = get_queue()
    mode = resolve_execution_mode_at_enqueue(
        contract="propagate",
        requested=LEASE_FREE_PROPAGATE_MODE,
    )
    j1 = queue.enqueue(
        thread_id="k1",
        turn_number=1,
        subject="p1",
        body="b",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="propagate",
        execution_mode=mode.mode,
        work_key=None,
    )
    j2 = queue.enqueue(
        thread_id="k2",
        turn_number=1,
        subject="p2",
        body="b",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="propagate",
        execution_mode=mode.mode,
        work_key=None,
    )
    c1 = queue.claim_next_concurrent()
    assert c1 is not None
    c2 = queue.claim_next_concurrent()
    assert c2 is not None
    assert {c1.job_id, c2.job_id} == {j1.job_id, j2.job_id}


def test_f13_health_projection_fields() -> None:
    queue = get_queue()
    _enqueue_conductor(queue)
    with (
        patch(
            "services.git_integration_worker.cursor_auto.gate_serialize.ledger_aligned_operator_occupancy",
            return_value=99,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_gate.sdk_dispatch_gate_stats",
            return_value={"limit": 3},
        ),
    ):
        snap = queue_admission_health()
    assert "concurrent_occupants" in snap
    assert "concurrent_pending_headroom_held" in snap
    assert snap["projection_only"] is True


def test_f15_headroom_hold_rising_edge_once() -> None:
    from services.git_integration_worker.cursor_auto.queue_health_events import (
        _headroom_hold_emitted,
        emit_concurrent_headroom_held,
    )

    reset_rising_edge_state_for_tests()
    emit_concurrent_headroom_held(
        job_id="j1",
        thread_id="t",
        work_key="todo:a",
        execution_mode=ISOLATED_LANE_CONDUCTOR_MODE,
    )
    emit_concurrent_headroom_held(
        job_id="j1",
        thread_id="t",
        work_key="todo:a",
        execution_mode=ISOLATED_LANE_CONDUCTOR_MODE,
    )
    assert "j1" in _headroom_hold_emitted


def test_f6_concurrent_never_via_claim_next() -> None:
    queue = get_queue()
    _enqueue_conductor(queue)
    assert queue.claim_next() is None


def test_ac4_missing_work_key_stays_serial() -> None:
    assert (
        declared_execution_mode(contract="investigate", lane="B", work_key=None)
        == "serial"
    )


def test_f3_fourth_job_stays_queued_at_gate_capacity() -> None:
    queue = get_queue()
    keys = [f"todo:k{i}" for i in range(4)]
    for i, wk in enumerate(keys):
        _enqueue_conductor(queue, work_key=wk, thread_id=f"t{i}", turn=1)
    assert queue.claim_next_concurrent() is not None
    assert queue.claim_next_concurrent() is not None
    assert queue.claim_next_concurrent() is not None
    fourth = queue.head_concurrent_queued()
    assert fourth is not None
    assert fourth.work_key == keys[3]
    assert fourth.status == "queued"


def test_f8_confer_lane_b_not_concurrent_class() -> None:
    assert (
        declared_execution_mode(contract="confer", lane="B", work_key="todo:x")
        == "serial"
    )


def test_f14_mode_declined_reason_in_enum() -> None:
    res = resolve_execution_mode_at_enqueue(
        contract="answer",
        requested=ISOLATED_LANE_CONDUCTOR_MODE,
    )
    assert res.reason == "predicate_unmet_requested_declined"


def test_f16_verify_contract_in_allowlist() -> None:
    from services.git_integration_worker.cursor_auto.execution_mode import (
        LANE_CONDUCTOR_CONTRACTS,
    )

    assert "verify" in LANE_CONDUCTOR_CONTRACTS
    assert (
        declared_execution_mode(contract="verify", lane="B", work_key="todo:v")
        == ISOLATED_LANE_CONDUCTOR_MODE
    )


def test_f5_allowlisted_contracts_not_propagate_in_seat_only() -> None:
    from libs.contract_vocab.records import nested_scope_contracts

    for c in ("investigate", "recon", "verify"):
        assert c in nested_scope_contracts()


def test_f12_nested_dispatch_thread_id_is_job_thread() -> None:
    import inspect

    from services.git_integration_worker.cursor_auto import handler

    src = inspect.getsource(handler.process_job)
    assert "job.thread_id" in src


def test_ac1_implement_and_conductor_join_allowlist_lane_a_stays_serial() -> None:
    from services.git_integration_worker.cursor_auto.execution_mode import (
        LANE_CONDUCTOR_CONTRACTS,
    )
    from services.git_integration_worker.cursor_auto.handler import _NESTED_CONTRACTS
    from services.git_integration_worker.cursor_auto.wire_map import (
        resolve_handoff_contract,
    )

    assert LANE_CONDUCTOR_CONTRACTS == frozenset(
        {"investigate", "recon", "verify", "implement", "conductor"}
    )
    assert (
        declared_execution_mode(contract="implement", lane="B", work_key="todo:impl")
        == ISOLATED_LANE_CONDUCTOR_MODE
    )
    assert (
        declared_execution_mode(contract="conductor", lane="B", work_key="todo:cond")
        == ISOLATED_LANE_CONDUCTOR_MODE
    )
    assert (
        declared_execution_mode(contract="implement", lane="A", work_key="todo:impl")
        == "serial"
    )
    assert (
        declared_execution_mode(contract="implement", lane="B", work_key=None)
        == "serial"
    )
    assert (
        declared_execution_mode(contract="seed", lane="B", work_key="todo:seed")
        == "serial"
    )
    assert resolve_handoff_contract("conductor") == "conductor"
    assert "conductor" in _NESTED_CONTRACTS
    assert "investigate" in LANE_CONDUCTOR_CONTRACTS


def test_ac1_two_implement_jobs_claim_together_same_key_does_not() -> None:
    queue = get_queue()
    _enqueue_conductor(
        queue, contract="implement", work_key="todo:probe-a", thread_id="lane-a"
    )
    _enqueue_conductor(
        queue, contract="implement", work_key="todo:probe-b", thread_id="lane-b"
    )
    same = _enqueue_conductor(
        queue, contract="implement", work_key="todo:probe-a", thread_id="lane-c"
    )
    c1 = queue.claim_next_concurrent()
    c2 = queue.claim_next_concurrent()
    assert c1 is not None and c2 is not None
    assert {c1.work_key, c2.work_key} == {"todo:probe-a", "todo:probe-b"}
    assert queue.claim_next_concurrent() is None
    assert queue.get(same.job_id).status == "queued"
    assert queue.claim_next() is None
    slots = queue.snapshot()["claimed_slots"]
    assert {slot["job_id"] for slot in slots} == {c1.job_id, c2.job_id}


def test_ac3_same_thread_supersede_candidate_and_drain_sees_both_claims() -> None:
    queue = get_queue()
    first = _enqueue_conductor(
        queue, contract="implement", work_key="todo:live", thread_id="same"
    )
    claimed = queue.claim_next_concurrent()
    assert claimed is not None and claimed.job_id == first.job_id
    second = _enqueue_conductor(
        queue,
        contract="implement",
        work_key="todo:next",
        thread_id="same",
        turn=2,
    )
    candidate = queue.supersede_candidate_for_thread("same")
    assert candidate is not None and candidate.job_id == first.job_id
    queue.mark_superseded(first.job_id, superseded_by=second.job_id)
    assert queue.get(first.job_id).status == "superseded"
    other = _enqueue_conductor(
        queue, contract="conductor", work_key="todo:other", thread_id="other"
    )
    live = queue.claim_next_concurrent()
    assert live is not None and live.job_id == second.job_id
    extra = queue.claim_next_concurrent()
    assert extra is not None and extra.job_id == other.job_id
    occupied = {row["op_id"] for row in queue.claimed_occupancy_ops()}
    assert occupied == {second.job_id, other.job_id}


def test_ac3_queued_implement_survives_restart_rehydrate() -> None:
    import asyncio

    from services.git_integration_worker.cursor_auto.job_reconcile import (
        reconcile_open_auto_jobs,
    )

    queue = get_queue()
    job = _enqueue_conductor(
        queue, contract="implement", work_key="todo:queued", thread_id="restart"
    )
    reset_queue_for_tests(durable=True)
    asyncio.run(reconcile_open_auto_jobs(post_bus=False, rehydrate=True))
    restored = get_queue().get(job.job_id)
    assert restored is not None
    assert restored.status == "queued"
    assert restored.execution_mode == ISOLATED_LANE_CONDUCTOR_MODE
    assert restored.work_key == "todo:queued"


def test_ac2_operator_slot_limit_default_and_named_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.cursor_sdk_gate import (
        OPERATOR_DISPATCH_CONCURRENCY_DEFAULT,
        OPERATOR_DISPATCH_CONCURRENCY_ENV,
        operator_dispatch_limit,
    )

    monkeypatch.delenv(OPERATOR_DISPATCH_CONCURRENCY_ENV, raising=False)
    assert operator_dispatch_limit() == OPERATOR_DISPATCH_CONCURRENCY_DEFAULT
    assert OPERATOR_DISPATCH_CONCURRENCY_DEFAULT == 3
    health = queue_admission_health()
    assert health["operator_slot_limit"] == 3
    assert health["operator_slot_limit_env"] == OPERATOR_DISPATCH_CONCURRENCY_ENV
    assert isinstance(health["operator_slots"], list)
    assert "serial_occupant_job_id" in health
    monkeypatch.setenv(OPERATOR_DISPATCH_CONCURRENCY_ENV, "6")
    assert operator_dispatch_limit() == 6
    assert queue_admission_health()["operator_slot_limit"] == 6


def test_f7_concurrent_claimed_jobs_have_distinct_threads() -> None:
    queue = get_queue()
    _enqueue_conductor(queue, thread_id="park-a", work_key="todo:a")
    _enqueue_conductor(queue, thread_id="park-b", work_key="todo:b")
    c1 = queue.claim_next_concurrent()
    c2 = queue.claim_next_concurrent()
    assert c1 is not None and c2 is not None
    assert c1.thread_id != c2.thread_id
