"""Hermetic tests for cursor-sdk ``resume_of`` plane (Phase B)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.cursor_home import dispatch_home_path
from services.git_integration_worker.cursor_sdk_events import FrontierSdkWorkerResumed
from services.git_integration_worker.cursor_sdk_resume import (
    closeout_qualifies_for_resume_retain,
    cursor_sdk_timeout_retain_s,
    dispatch_retain_active,
    load_resume_run_context,
    persist_resume_retain,
    persist_timeout_retain,
    record_resolved_store_roots,
    reject_resume_if_ineligible,
    resume_eligibility_reason,
    resume_retain_active,
    start_or_resume_agent,
    timeout_retain_active,
)
from services.git_integration_worker.cursor_sdk_worktree_prune import (
    prune_dispatch_worktree,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    lookup_dispatch_worktree,
    register_dispatch_worktree,
    touch_lane_worktree_dispatch,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    dispatch_id = str(overrides.pop("dispatch_id", "child-disp"))
    base = {
        "thread_id": "thread-resume",
        "model": "cursor/composer-2.5",
        "dispatch_id": dispatch_id,
        "execution_id": f"exec-{dispatch_id}",
        "message": f"continue-{dispatch_id}",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _insert_parent_row(
    *,
    dispatch_id: str = "parent-disp",
    status: str = "failed",
    state_root: str | None = "/tmp/state-root",
    sdk_agent_id: str | None = "agent-parent",
    record_json: dict | None = None,
    terminal_at: str | None = None,
) -> None:
    if state_root:
        store = Path(state_root)
        store.mkdir(parents=True, exist_ok=True)
        (store / ".keep").write_text("")
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id=dispatch_id, message="parent")
    fp = ledger.fingerprint(req)
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            "message_present, status, record_json, state_root, sdk_agent_id, "
            "terminal_status, terminal_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (
                dispatch_id,
                fp,
                req.thread_id,
                req.execution_id,
                "composer-2.5",
                status,
                json.dumps(record_json or {}),
                state_root,
                sdk_agent_id,
                status if status in {"completed", "failed", "cancelled"} else None,
                terminal_at,
            ),
        )


def test_ledger_resume_of_column_on_child_insert() -> None:
    _insert_parent_row()
    ledger = CursorDispatchLedger.instance()
    child = _req(dispatch_id="child-disp", resume_of="parent-disp")
    fp = ledger.fingerprint(child)
    from services.git_integration_worker.models.cursor_api import (
        CursorDispatchResponse,
    )

    assert (
        ledger.admit(
            req=child,
            fingerprint=fp,
            execution_id=child.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=CursorDispatchResponse(
                admitted=True,
                dispatch_id=child.dispatch_id,
                thread_id=child.thread_id,
                model_id="composer-2.5",
            ),
        )
        is None
    )
    with _connect() as conn:
        row = conn.execute(
            "SELECT resume_of FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("child-disp",),
        ).fetchone()
    assert row["resume_of"] == "parent-disp"


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("missing", "parent_missing"),
        ("running", "parent_still_live"),
        ("no_state_root", "state_root_missing"),
        ("no_agent_id", "sdk_agent_id_missing"),
    ],
)
def test_resume_eligibility_reasons(
    setup: str,
    reason: str,
    tmp_path: Path,
) -> None:
    if setup == "running":
        _insert_parent_row(status="running")
    elif setup == "no_state_root":
        _insert_parent_row(state_root=None)
    elif setup == "no_agent_id":
        _insert_parent_row(sdk_agent_id=None)
    elif setup == "missing":
        pass
    ledger = CursorDispatchLedger.instance()
    assert resume_eligibility_reason(ledger, parent_id="parent-disp") == reason


def test_state_root_absent_on_disk_reason(tmp_path: Path) -> None:
    state_path = tmp_path / "missing-dir"
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="parent-disp", message="parent")
    fp = ledger.fingerprint(req)
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            "message_present, status, state_root, sdk_agent_id) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (
                "parent-disp",
                fp,
                req.thread_id,
                req.execution_id,
                "composer-2.5",
                "failed",
                str(state_path),
                "agent-parent",
            ),
        )
    assert (
        resume_eligibility_reason(ledger, parent_id="parent-disp")
        == "state_root_absent_on_disk"
    )


def test_completed_parent_eligible_with_store(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    (store / "agent.db").write_text("x")
    _insert_parent_row(status="completed", state_root=str(store))
    ledger = CursorDispatchLedger.instance()
    assert resume_eligibility_reason(ledger, parent_id="parent-disp") is None


def test_cancelled_parent_eligible_with_store(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    (store / "agent.db").write_text("x")
    _insert_parent_row(status="cancelled", state_root=str(store))
    ledger = CursorDispatchLedger.instance()
    assert resume_eligibility_reason(ledger, parent_id="parent-disp") is None


def test_reject_resume_ineligible_envelope() -> None:
    child = _req(resume_of="missing-parent")
    response = reject_resume_if_ineligible(child)
    assert response is not None
    body = json.loads(response.body.decode())
    assert body["code"] == "CURSOR_RESUME_INELIGIBLE"
    assert body["data"]["reason"] == "parent_missing"
    assert body["retryable"] is False


def test_timeout_retain_blocks_prune(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    wt_path = tmp_path / "wt"
    wt_path.mkdir()
    register_dispatch_worktree(
        dispatch_id="parent-disp",
        worktree_path=wt_path,
        branch_name="cursor-sdk/test",
        branch_point="abc123",
    )
    _insert_parent_row(
        dispatch_id="parent-disp",
        state_root=str(tmp_path / "state"),
        record_json={"timeout_retain": True},
        terminal_at=(datetime.now(UTC) - timedelta(seconds=10)).isoformat(),
    )
    monkeypatch.setenv("CURSOR_SDK_TIMEOUT_RETAIN_S", "3600")
    assert timeout_retain_active(dispatch_id="parent-disp")
    result = prune_dispatch_worktree(
        dispatch_id="parent-disp",
        source_repo=source_repo,
    )
    assert result.pruned is False
    assert lookup_dispatch_worktree(dispatch_id="parent-disp") is not None


def test_non_timeout_failed_still_prunes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    (source_repo / ".git").mkdir()
    wt_path = tmp_path / "wt"
    wt_path.mkdir()
    register_dispatch_worktree(
        dispatch_id="fail-disp",
        worktree_path=wt_path,
        branch_name="cursor-sdk/fail",
        branch_point="abc123",
    )
    _insert_parent_row(
        dispatch_id="fail-disp",
        state_root=str(tmp_path / "state"),
        record_json={},
        terminal_at=datetime.now(UTC).isoformat(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_prune.subprocess.run",
        lambda *args, **kwargs: MagicMock(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_prune.is_worktree_dirty",
        lambda _path: False,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_prune.branch_state",
        lambda *args, **kwargs: MagicMock(
            safe_to_delete=True, commits_ahead=0, head_sha="deadbeef"
        ),
    )
    assert not timeout_retain_active(dispatch_id="fail-disp")
    result = prune_dispatch_worktree(
        dispatch_id="fail-disp",
        source_repo=source_repo,
    )
    assert result.pruned is True


def test_parent_row_byte_stable_after_child_admit() -> None:
    _insert_parent_row(
        record_json={"note": "stable"},
        terminal_at=datetime.now(UTC).isoformat(),
    )
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        before = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("parent-disp",),
        ).fetchone()
        before_blob = dict(before)

    child = _req(dispatch_id="child-disp", resume_of="parent-disp")
    fp = ledger.fingerprint(child)
    from services.git_integration_worker.models.cursor_api import CursorDispatchResponse

    ledger.admit(
        req=child,
        fingerprint=fp,
        execution_id=child.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=child.dispatch_id,
            thread_id=child.thread_id,
            model_id="composer-2.5",
        ),
    )
    with ledger._connect() as conn:
        after = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("parent-disp",),
        ).fetchone()
        after_blob = dict(after)
    assert before_blob == after_blob


def test_lane_worktree_reuse_updates_last_dispatch() -> None:
    wt_path = Path("/tmp/wt-lane")
    register_dispatch_worktree(
        dispatch_id="parent-disp",
        worktree_path=wt_path,
        branch_name="cursor-sdk/lane-t-reuse",
        branch_point="abc",
        thread_id="t-reuse",
    )
    touch_lane_worktree_dispatch(thread_id="t-reuse", dispatch_id="child-disp")
    child = lookup_dispatch_worktree(dispatch_id="child-disp")
    assert child is not None
    assert child.worktree_path == wt_path
    assert child.thread_id == "t-reuse"
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        lookup_lane_worktree,
    )

    lane = lookup_lane_worktree(thread_id="t-reuse")
    assert lane is not None
    assert lane.last_dispatch_id == "child-disp"


def test_start_or_resume_agent_branches() -> None:
    client = MagicMock()
    resume_agent = MagicMock(return_value=MagicMock(agent_id="agent-1"))
    create_agent = MagicMock(return_value=MagicMock(agent_id="agent-2"))
    client.resume_agent = resume_agent
    client.create_agent = create_agent
    run = MagicMock(id="run-1")
    resume_agent.return_value.send = MagicMock(return_value=run)
    create_agent.return_value.send = MagicMock(return_value=run)
    options = MagicMock()
    from services.git_integration_worker.cursor_sdk_resume import ResumeRunContext

    ctx = ResumeRunContext(
        resume_of="parent",
        state_root="/tmp/state",
        sdk_agent_id="agent-parent",
    )
    agent, got_run = start_or_resume_agent(
        client=client,
        agent_options=options,
        prompt="continue",
        resume_ctx=ctx,
    )
    resume_agent.assert_called_once_with("agent-parent", options)
    create_agent.assert_not_called()
    assert got_run is run

    create_agent.reset_mock()
    resume_agent.reset_mock()
    start_or_resume_agent(
        client=client,
        agent_options=options,
        prompt="fresh",
        resume_ctx=None,
    )
    create_agent.assert_called_once_with(options)
    resume_agent.assert_not_called()


def test_load_resume_run_context() -> None:
    _insert_parent_row()
    ledger = CursorDispatchLedger.instance()
    child = _req(dispatch_id="child-disp", resume_of="parent-disp")
    fp = ledger.fingerprint(child)
    from services.git_integration_worker.models.cursor_api import CursorDispatchResponse

    ledger.admit(
        req=child,
        fingerprint=fp,
        execution_id=child.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=child.dispatch_id,
            thread_id=child.thread_id,
            model_id="composer-2.5",
        ),
    )
    ctx = load_resume_run_context(dispatch_id="child-disp")
    assert ctx is not None
    assert ctx.resume_of == "parent-disp"
    assert ctx.sdk_agent_id == "agent-parent"


def test_empty_state_root_dir_eligible_via_home_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty bridge-state column + HOME sdk-agent-store → eligible; store rewritten."""
    homes_root = tmp_path / "homes"
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(homes_root))
    parent_id = "parent-empty-bridge"
    empty_bridge = tmp_path / "empty-bridge-state"
    empty_bridge.mkdir()
    parent_home = dispatch_home_path(parent_id)
    store = (
        parent_home
        / ".cursor"
        / "projects"
        / "mnt-torus-projects-repo"
        / "sdk-agent-store"
    )
    store.mkdir(parents=True, exist_ok=True)
    (store / "agents.db").write_text("x")

    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id=parent_id, message="parent")
    fp = ledger.fingerprint(req)
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            "message_present, status, state_root, sdk_agent_id) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (
                parent_id,
                fp,
                req.thread_id,
                req.execution_id,
                "composer-2.5",
                "completed",
                str(empty_bridge),
                "agent-parent",
            ),
        )

    assert resume_eligibility_reason(ledger, parent_id=parent_id) is None

    child = _req(dispatch_id="child-resume", resume_of=parent_id)
    child_fp = ledger.fingerprint(child)
    from services.git_integration_worker.models.cursor_api import CursorDispatchResponse

    ledger.admit(
        req=child,
        fingerprint=child_fp,
        execution_id=child.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=child.dispatch_id,
            thread_id=child.thread_id,
            model_id="composer-2.5",
        ),
    )
    ctx = load_resume_run_context(dispatch_id="child-resume")
    assert ctx is not None
    assert ctx.state_root == str(store)

    record_resolved_store_roots(
        parent_id=parent_id,
        child_id="child-resume",
        parent_state_root=str(empty_bridge),
    )
    with ledger._connect() as conn:
        parent_row = conn.execute(
            "SELECT state_root FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
        child_row = conn.execute(
            "SELECT state_root FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("child-resume",),
        ).fetchone()
    assert parent_row["state_root"] == str(store)
    assert child_row["state_root"] == str(store)


def test_resume_retain_blocks_prune_for_completed_conductor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    wt_path = tmp_path / "wt"
    wt_path.mkdir()
    register_dispatch_worktree(
        dispatch_id="parent-disp",
        worktree_path=wt_path,
        branch_name="cursor-sdk/test",
        branch_point="abc123",
    )
    store = tmp_path / "state"
    _insert_parent_row(
        dispatch_id="parent-disp",
        status="completed",
        state_root=str(store),
        record_json={"resume_retain": True},
        terminal_at=(datetime.now(UTC) - timedelta(seconds=10)).isoformat(),
    )
    monkeypatch.setenv("CURSOR_SDK_TIMEOUT_RETAIN_S", "3600")
    assert resume_retain_active(dispatch_id="parent-disp")
    assert dispatch_retain_active(dispatch_id="parent-disp")
    result = prune_dispatch_worktree(
        dispatch_id="parent-disp",
        source_repo=source_repo,
    )
    assert result.pruned is False


def test_closeout_qualifies_for_resume_retain() -> None:
    assert closeout_qualifies_for_resume_retain(
        closeout_body="status: complete\nstop: ROW_PINNED",
        packet_kind=None,
    )
    assert closeout_qualifies_for_resume_retain(
        closeout_body="status: complete",
        packet_kind="conductor",
    )
    assert not closeout_qualifies_for_resume_retain(
        closeout_body="status: complete",
        packet_kind=None,
    )


def test_persist_resume_retain_merges_record_json() -> None:
    _insert_parent_row(record_json={"existing": True})
    persist_resume_retain(dispatch_id="parent-disp")
    with _connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("parent-disp",),
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data["resume_retain"] is True
    assert data["existing"] is True


def test_persist_timeout_retain_merges_record_json() -> None:
    _insert_parent_row(record_json={"existing": True})
    persist_timeout_retain(dispatch_id="parent-disp")
    with _connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("parent-disp",),
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data["timeout_retain"] is True
    assert data["resume_retain"] is True
    assert data["existing"] is True


def test_resume_admission_http_422(client: TestClient | None = None) -> None:
    app = create_app()
    client = TestClient(app)
    response = client.post(
        "/api/v1/cursor/dispatch",
        json={
            "thread_id": "t",
            "model": "cursor/composer-2.5",
            "dispatch_id": "child-http",
            "execution_id": "exec-child-http",
            "message": "resume me",
            "resume_of": "missing-parent",
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "CURSOR_RESUME_INELIGIBLE"


def test_resumed_event_factory_payload() -> None:
    event = FrontierSdkWorkerResumed(
        dispatch_id="child",
        resume_of="parent",
        sdk_agent_id="agent-1",
        state_root="/tmp/state",
        thread_id="t1",
        execution_id="e1",
    )
    assert event.signal == "frontier.sdk.worker.resumed"
    assert event.payload["dispatch_id"] == "child"
    assert event.payload["resume_of"] == "parent"
    assert event.role == "observation"


def test_timeout_retain_default_matches_home_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURSOR_SDK_TIMEOUT_RETAIN_S", raising=False)
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_RETENTION_DAYS", "7")
    assert cursor_sdk_timeout_retain_s() == 7 * 86400
