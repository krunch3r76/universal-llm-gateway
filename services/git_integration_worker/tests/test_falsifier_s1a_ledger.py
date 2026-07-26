"""Bound-invariant falsifiers — S1a ledger (F-A2, F-A3, F-A6, AC3, AC4, AC5)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.cursor_sdk_workspace import (
    lane_a_lease_key,
    resolve_dispatch_workspace,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


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
    source_repo: str = "/repo",
    lease_key: str | None = None,
    contract: str = "implement",
    nest_under: str | None = None,
) -> CursorDispatchResponse | None:
    return ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req),
        source_repo=source_repo,
        lease_key=lease_key,
        contract=contract,
        worker_instance="worker-a",
        nest_under=nest_under,
    )


def test_falsifier_ac3_ledger_distinct_lease_key_parallel_admit() -> None:
    """AC3 (ledger layer): distinct lease_key values both reach admitted."""
    ledger = CursorDispatchLedger.instance()
    shared_repo = "/mnt/torus/projects/universal-llm-gateway"
    key_a = "/tmp/worktree-a"
    key_b = "/tmp/worktree-b"

    assert (
        _admit(
            ledger,
            _req(dispatch_id="iso-a"),
            source_repo=shared_repo,
            lease_key=key_a,
        )
        is None
    )
    second = _admit(
        ledger,
        _req(dispatch_id="iso-b"),
        source_repo=shared_repo,
        lease_key=key_b,
    )
    assert second is None

    with _connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id, status, lease_key FROM cursor_sdk_dispatches "
            "WHERE dispatch_id IN ('iso-a','iso-b') ORDER BY dispatch_id"
        ).fetchall()
    assert len(rows) == 2
    assert all(r["status"] == "admitted" for r in rows)
    assert {r["lease_key"] for r in rows} == {key_a, key_b}


def test_falsifier_ac4_lane_a_same_repo_queues() -> None:
    """AC4: same lease_key — second writer remains queued."""
    ledger = CursorDispatchLedger.instance()
    repo = "/mnt/torus/projects/universal-llm-gateway"
    key = lane_a_lease_key(Path(repo))

    _admit(ledger, _req(dispatch_id="lane-a-1"), source_repo=repo, lease_key=key)
    queued = _admit(
        ledger, _req(dispatch_id="lane-a-2"), source_repo=repo, lease_key=key
    )
    assert queued is not None
    assert queued.status == "queued"


def test_falsifier_ac5_nest_park_transfer_holder_on_shared_lease_key() -> None:
    """AC5: nest park on shared lease_key; sibling cannot steal while parent parked."""
    ledger = CursorDispatchLedger.instance()
    repo = "/repo"
    key = "/repo"

    _admit(ledger, _req(dispatch_id="parent"), source_repo=repo, lease_key=key)
    assert (
        _admit(
            ledger,
            _req(dispatch_id="child"),
            source_repo=repo,
            lease_key=key,
            nest_under="parent",
        )
        is None
    )

    with _connect() as conn:
        parent = conn.execute(
            "SELECT status, park_child_dispatch_id FROM cursor_sdk_dispatches "
            "WHERE dispatch_id='parent'"
        ).fetchone()
        child = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id='child'"
        ).fetchone()
    assert parent["status"] == "parked_waiting"
    assert parent["park_child_dispatch_id"] == "child"
    assert child["status"] == "admitted"

    sibling = _admit(
        ledger, _req(dispatch_id="sibling"), source_repo=repo, lease_key=key
    )
    assert sibling is not None
    assert sibling.status == "queued"

    assert ledger.has_parked_parent(lease_key=key)
    assert ledger.promote_next_queued(lease_key=key) is None

    assert ledger.restore_from_park(parent_id="parent") == repo


def test_falsifier_f_a2_lane_a_zero_runtime_delta() -> None:
    """F-A2: Lane-A lease_key equals resolved source_repo; queuing unchanged."""
    cfg = load_config()
    req = _req()
    workspace = resolve_dispatch_workspace(req, cfg)
    assert workspace == cfg.dispatch_workspace

    key = lane_a_lease_key(cfg.source_repo)
    assert key == str(cfg.source_repo.resolve())

    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        _req(dispatch_id="delta-1"),
        source_repo=key,
        lease_key=key,
    )
    queued = _admit(
        ledger,
        _req(dispatch_id="delta-2"),
        source_repo=key,
        lease_key=key,
    )
    assert queued is not None
    assert queued.status == "queued"
    assert queued.queue_position == 1


def test_falsifier_f_a6_no_lease_lookup_reads_source_repo() -> None:
    """F-A6: writer-conflict / queue / nest-holder SQL uses lease_key, not source_repo."""
    ledger_path = (
        Path(__file__).resolve().parents[1] / "cursor_dispatch_ledger.py"
    )
    source = ledger_path.read_text(encoding="utf-8")

    lease_where_patterns = [
        r"WHERE lease_key=\?",
        r"WHERE lease_key=\? AND COALESCE\(read_only,0\)=0",
    ]
    for pattern in lease_where_patterns:
        assert re.search(pattern, source), f"missing lease lookup pattern: {pattern}"

    forbidden = re.findall(
        r"WHERE source_repo=\? AND COALESCE\(read_only,0\)=0\s+"
        r"AND status IN \('admitted','running'\)",
        source,
    )
    assert not forbidden, "writer-conflict lookup still keys on source_repo"

    forbidden_queue = re.findall(
        r"WHERE source_repo=\? AND COALESCE\(read_only,0\)=0 AND status='queued'",
        source,
    )
    assert not forbidden_queue, "queue lookup still keys on source_repo"

    forbidden_parked = re.findall(
        r"WHERE source_repo=\? AND COALESCE\(read_only,0\)=0\s+AND status=\?",
        source,
    )
    assert not forbidden_parked, "parked-parent lookup still keys on source_repo"


def test_falsifier_f_a3_no_fifo_priority_lane_api() -> None:
    """F-A3: S1a adds no FifoCapacityGate priority-lane / preempt API."""
    gate_path = (
        Path(__file__).resolve().parents[3]
        / "libs"
        / "universal_concurrency"
        / "fifo_capacity_gate.py"
    )
    sdk_gate_path = Path(__file__).resolve().parents[1] / "cursor_sdk_gate.py"
    combined = gate_path.read_text(encoding="utf-8") + sdk_gate_path.read_text(
        encoding="utf-8"
    )
    forbidden = [
        r"def\s+priority",
        r"async def\s+priority",
        r"def\s+preempt",
        r"async def\s+preempt",
        r"priority_lane",
        r"attended_preempt",
    ]
    for pattern in forbidden:
        assert not re.search(pattern, combined), (
            f"FifoCapacityGate priority/preempt surface leaked: {pattern}"
        )
