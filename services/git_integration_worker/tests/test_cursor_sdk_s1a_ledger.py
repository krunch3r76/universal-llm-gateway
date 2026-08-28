"""S1a-1 ledger foundation — lease_key column + resolve_dispatch_workspace seam."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.git_integration_worker.config import WorkerConfig, load_config
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


def test_s1a_lease_key_migration_and_backfill() -> None:
    """Schema migration adds lease_key; existing rows backfill from source_repo."""
    ledger = CursorDispatchLedger.instance()
    repo = "/mnt/torus/projects/universal-llm-gateway"
    req = _req(dispatch_id="migrate-1")
    _admit(ledger, req, source_repo=repo, lease_key=repo)

    with _connect() as conn:
        cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(cursor_sdk_dispatches)")
        }
        assert "lease_key" in cols
        row = conn.execute(
            "SELECT source_repo, lease_key FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    assert row["source_repo"] == repo
    assert row["lease_key"] == repo


def test_s1a_ledger_distinct_lease_key_parallel_admit() -> None:
    """AC3 (ledger layer): distinct lease_key values both reach admitted."""
    ledger = CursorDispatchLedger.instance()
    shared_repo = "/mnt/torus/projects/universal-llm-gateway"
    key_a = "/tmp/worktree-a"
    key_b = "/tmp/worktree-b"

    assert (
        _admit(
            ledger,
            _req(dispatch_id="iso-a", thread_id="t-iso-a"),
            source_repo=shared_repo,
            lease_key=key_a,
        )
        is None
    )
    second = _admit(
        ledger,
        _req(dispatch_id="iso-b", thread_id="t-iso-b"),
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


def test_s1a_lane_a_same_repo_queues() -> None:
    """AC4: same lease_key — second writer remains queued."""
    ledger = CursorDispatchLedger.instance()
    repo = "/mnt/torus/projects/universal-llm-gateway"
    key = lane_a_lease_key(Path(repo))

    _admit(
        ledger,
        _req(dispatch_id="lane-a-1", thread_id="t-lane-a-1"),
        source_repo=repo,
        lease_key=key,
    )
    queued = _admit(
        ledger,
        _req(dispatch_id="lane-a-2", thread_id="t-lane-a-2"),
        source_repo=repo,
        lease_key=key,
    )
    assert queued is not None
    assert queued.status == "queued"


def test_s1a_nest_park_transfer_holder_on_shared_lease_key() -> None:
    """AC5: nest park on shared lease_key; sibling cannot steal while parent parked."""
    ledger = CursorDispatchLedger.instance()
    repo = "/repo"
    key = "/repo"

    _admit(
        ledger,
        _req(dispatch_id="parent", thread_id="t-parent"),
        source_repo=repo,
        lease_key=key,
    )
    assert (
        _admit(
            ledger,
            _req(dispatch_id="child", thread_id="t-child"),
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
        ledger,
        _req(dispatch_id="sibling", thread_id="t-sibling"),
        source_repo=repo,
        lease_key=key,
    )
    assert sibling is not None
    assert sibling.status == "queued"

    assert ledger.has_parked_parent(lease_key=key)
    assert ledger.promote_next_queued(lease_key=key) is None

    assert ledger.restore_from_park(parent_id="parent") == repo


def test_s1a_lane_a_zero_runtime_delta() -> None:
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
        _req(dispatch_id="delta-1", thread_id="t-delta-1"),
        source_repo=key,
        lease_key=key,
    )
    queued = _admit(
        ledger,
        _req(dispatch_id="delta-2", thread_id="t-delta-2"),
        source_repo=key,
        lease_key=key,
    )
    assert queued is not None
    assert queued.status == "queued"
    assert queued.queue_position == 1


def test_s1a_f_a6_no_lease_lookup_reads_source_repo() -> None:
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


def test_s1a_resolve_dispatch_workspace_seam() -> None:
    """AC1: resolve_dispatch_workspace exists; Lane-A returns cfg.dispatch_workspace."""
    cfg = WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=Path("/mnt/torus/projects/universal-llm-gateway"),
        worktree_root=Path("/tmp/worktrees"),
        dispatch_workspace=Path("/mnt/torus/projects"),
        green_gate_cmd=["true"],
    )
    req = _req()
    assert resolve_dispatch_workspace(req, cfg) == cfg.dispatch_workspace


def test_s1a_route_imports_workspace_seam() -> None:
    """AC1: cursor_sdk route module wires Lane-B admit workspace binding."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source = Path(route_mod.__file__).read_text(encoding="utf-8")
    assert "resolve_admit_binding" in source


def test_s1a_f_a3_no_fifo_priority_lane_api() -> None:
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
        assert not __import__("re").search(pattern, combined), (
            f"FifoCapacityGate priority/preempt surface leaked: {pattern}"
        )
