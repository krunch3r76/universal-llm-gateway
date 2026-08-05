"""Falsifiers for cursor-sdk capacity/lease honesty M0 (F1, F2, F4, I1, M0b)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.cursor_sdk_capacity_invariant import evaluate_i1
from services.git_integration_worker.cursor_sdk_capture_divergence import (
    read_only_repo_diff_violation,
)
from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    resolve_closeout_capture_fields,
)
from services.git_integration_worker.cursor_sdk_gate import (
    acquire_sdk_dispatch_slot,
    release_sdk_dispatch_slot,
    sdk_dispatch_gate_stats,
)
from services.git_integration_worker.cursor_sdk_lane_regime import set_lane_b_regime
from services.git_integration_worker.cursor_sdk_workspace import lane_a_lease_key
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.routes.cursor_sdk import (
    _caller_explicitly_set_read_only,
    _effective_read_only,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    set_lane_b_regime(active=False)
    yield
    CursorDispatchLedger._instance = None
    set_lane_b_regime(active=False)


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "t1",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-1",
        "execution_id": "exec-disp-1",
        "message": "hello",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admission(req: CursorDispatchRequest) -> CursorDispatchResponse:
    return CursorDispatchResponse(
        admitted=True,
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        model_id="composer-2.5",
    )


def _admit(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    source_repo: str,
    lease_key: str,
    contract: str = "implement",
    read_only: bool = False,
    caller_agent: str | None = None,
) -> CursorDispatchResponse | None:
    return ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=caller_agent,
        resolved_model="composer-2.5",
        admission=_admission(req),
        source_repo=source_repo,
        lease_key=lease_key,
        contract=contract,
        read_only=read_only,
        worker_instance="worker-a",
    )


def test_f1_write_capacity_surface_and_queued_on_projection() -> None:
    """F1: write_capacity=1 and queued rows carry write_lease attribution."""
    ledger = CursorDispatchLedger.instance()
    repo = "/mnt/torus/projects/universal-llm-gateway"
    key = lane_a_lease_key(Path(repo))

    _admit(ledger, _req(dispatch_id="holder"), source_repo=repo, lease_key=key)
    queued = _admit(
        ledger, _req(dispatch_id="waiter"), source_repo=repo, lease_key=key
    )
    assert queued is not None
    assert queued.status == "queued"

    gate = sdk_dispatch_gate_stats()
    assert gate["write_capacity"] == 1

    snap = ledger.lease_snapshot(source_repo=repo)
    assert snap["queue_depth"] == 1
    assert len(snap["queued"]) == 1
    assert snap["queued"][0]["queued_on"] == f"write_lease:{key}"
    assert snap["queued"][0]["dispatch_id"] == "waiter"


def test_f2_read_only_consult_runs_while_write_holder_active() -> None:
    """F2: operator read-only consult is lease-exempt; write still queues."""
    ledger = CursorDispatchLedger.instance()
    repo = "/repo"
    key = lane_a_lease_key(Path(repo))

    auto_req = _req(
        dispatch_id="auto-holder",
        thread_id="auto-thread",
    )
    _admit(
        ledger,
        auto_req,
        source_repo=repo,
        lease_key=key,
        contract="implement",
        caller_agent="cursor-auto",
    )

    consult_req = _req(dispatch_id="op-consult", thread_id="op-consult")
    consult = _admit(
        ledger,
        consult_req,
        source_repo=repo,
        lease_key=key,
        contract="consult",
        read_only=True,
        caller_agent="cursor",
    )
    assert consult is None

    write_req = _req(dispatch_id="op-write", thread_id="op-write")
    queued = _admit(
        ledger,
        write_req,
        source_repo=repo,
        lease_key=key,
        contract="implement",
        read_only=False,
        caller_agent="cursor",
    )
    assert queued is not None
    assert queued.status == "queued"
    snap = ledger.lease_snapshot(source_repo=repo)
    assert snap["queued"][0]["queued_on"] == f"write_lease:{key}"


def test_f4_consult_defaults_read_only_and_explicit_opt_in_writes() -> None:
    """F4: omitted consult is read-only; explicit false participates in lease."""
    consult_default = _req(dispatch_id="c-default")
    assert not _caller_explicitly_set_read_only(consult_default)
    assert _effective_read_only(consult_default, "consult") is True

    consult_explicit = CursorDispatchRequest.model_validate(
        {
            "thread_id": "t1",
            "model": "cursor/composer-2.5",
            "dispatch_id": "c-explicit",
            "execution_id": "exec-c-explicit",
            "message": "hello",
            "read_only": False,
        }
    )
    assert _caller_explicitly_set_read_only(consult_explicit)
    assert _effective_read_only(consult_explicit, "consult") is False

    ledger = CursorDispatchLedger.instance()
    repo = "/repo"
    key = lane_a_lease_key(Path(repo))
    _admit(ledger, _req(dispatch_id="holder-2"), source_repo=repo, lease_key=key)
    queued = _admit(
        ledger,
        consult_explicit,
        source_repo=repo,
        lease_key=key,
        contract="consult",
        read_only=False,
    )
    assert queued is not None
    assert queued.status == "queued"

    with _connect() as conn:
        bad = conn.execute(
            "SELECT COUNT(*) AS n FROM cursor_sdk_dispatches "
            "WHERE COALESCE(read_only,0)=1 AND status IN ('admitted','running')"
        ).fetchone()
    assert int(bad["n"]) == 0


def test_f4_implement_read_only_conflict_still_rejected() -> None:
    """F4: explicit read_only=true with implement remains invalid."""
    req = CursorDispatchRequest.model_validate(
        {
            "thread_id": "t1",
            "model": "cursor/composer-2.5",
            "dispatch_id": "impl-ro",
            "execution_id": "exec-impl-ro",
            "message": "hello",
            "read_only": True,
            "handoff_contract": "implement",
        }
    )
    assert _effective_read_only(req, "implement") is True


def test_i1_evaluate_i1_scaffold() -> None:
    assert evaluate_i1(1, 3, headroom=4) == "ok"
    assert evaluate_i1(1, 3, headroom=3) == "clamp"


def test_capacity_wait_emits_once_per_wait_entry() -> None:
    wait_calls: list[str] = []

    async def exercise() -> None:
        await acquire_sdk_dispatch_slot(dispatch_id="cap-a", timeout=5)
        try:
            await acquire_sdk_dispatch_slot(
                dispatch_id="cap-b",
                timeout=0.05,
                on_wait=lambda: wait_calls.append("wait"),
            )
        except TimeoutError:
            pass
        await release_sdk_dispatch_slot(dispatch_id="cap-a")

    asyncio.run(exercise())
    assert wait_calls == ["wait"]


def test_capacity_wait_skipped_on_immediate_acquire() -> None:
    wait_calls: list[str] = []

    async def exercise() -> None:
        await acquire_sdk_dispatch_slot(
            dispatch_id="cap-immediate",
            timeout=5,
            on_wait=lambda: wait_calls.append("wait"),
        )
        await release_sdk_dispatch_slot(dispatch_id="cap-immediate")

    asyncio.run(exercise())
    assert wait_calls == []


def test_read_only_closeout_repo_diff_violation_and_control() -> None:
    change_set = ChangeSet(created=("foo.py",), modified=(), deleted=())
    token = read_only_repo_diff_violation(
        read_only=True,
        change_set=change_set,
        source_repo=Path("/repo"),
    )
    assert token is not None
    assert token.startswith("divergence:repo_diff_paths_unattributed:")

    assert (
        read_only_repo_diff_violation(
            read_only=True,
            change_set=ChangeSet(created=(), modified=(), deleted=()),
            source_repo=Path("/repo"),
        )
        is None
    )

    emitted: list[tuple[str, str, str]] = []

    with patch(
        "services.git_integration_worker.cursor_sdk_events.emit_sdk_capture_divergence_observed",
        side_effect=lambda **kwargs: emitted.append(
            (kwargs["dispatch_id"], kwargs["thread_id"], kwargs["deviation"])
        ),
    ):
        capture_status, divergence_reason, deviations, _manifest = (
            resolve_closeout_capture_fields(
                deliverables_expected=False,
                baseline=None,
                files_expected=[],
                degraded_reason=None,
                change_set=change_set,
                divergent_rels=(),
                source_repo=Path("/repo"),
                cortex_root=Path("/tmp/cortex"),
                read_only=True,
                dispatch_id="d-ro",
                thread_id="t-ro",
            )
        )
    assert divergence_reason == token
    assert token in deviations
    assert capture_status == "partial"
    assert emitted == [("d-ro", "t-ro", token)]
