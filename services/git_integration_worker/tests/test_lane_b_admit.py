"""Lane-B S2 — lane field + admit tests (AC-S2.*)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    DispatchConflict,
)
from services.git_integration_worker.cursor_sdk_lane_regime import (
    lane_b_regime_active,
    set_lane_b_regime,
)
from services.git_integration_worker.cursor_sdk_lane_select import select_lane
from services.git_integration_worker.cursor_sdk_workspace import (
    default_write_path_is_lane_a,
    resolve_dispatch_workspace,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    lookup_dispatch_worktree,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    set_lane_b_regime(active=False)
    yield
    CursorDispatchLedger._instance = None
    set_lane_b_regime(active=False)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source_repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    return repo


@pytest.fixture
def worker_cfg(tmp_path: Path, git_repo: Path) -> WorkerConfig:
    wt_root = tmp_path / "worktrees"
    wt_root.mkdir()
    dispatch_ws = tmp_path / "dispatch_ws"
    dispatch_ws.mkdir()
    return WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=git_repo,
        worktree_root=wt_root,
        dispatch_workspace=dispatch_ws,
        green_gate_cmd=["true"],
    )


@pytest.fixture
def client(
    worker_cfg: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(worker_cfg.source_repo))
    monkeypatch.setenv("GIT_INTEGRATION_WORKTREE_ROOT", str(worker_cfg.worktree_root))
    monkeypatch.setenv(
        "GIT_INTEGRATION_DISPATCH_WORKSPACE", str(worker_cfg.dispatch_workspace)
    )
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setattr(route_mod, "_CONFIG", worker_cfg)
    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"setting_sources": ["projectSettings"]},
    )

    async def _noop_acquire(**kwargs: object) -> str:
        return str(kwargs.get("dispatch_id") or "slot")

    monkeypatch.setattr(route_mod, "acquire_sdk_dispatch_slot", _noop_acquire)
    monkeypatch.setattr(
        route_mod, "release_or_restore_for_child_sync", lambda *_a, **_k: "released"
    )
    monkeypatch.setattr(route_mod, "emit_implement_closeout_trigger", MagicMock())

    app = create_app()
    app.state.worker_config = worker_cfg
    return TestClient(app)


def _body(**overrides: Any) -> dict[str, Any]:
    source_ref = overrides.pop("source_ref", "todo:s2")
    base = {
        "thread_id": "6661",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-s2",
        "execution_id": "exec-disp-s2",
        "handoff_contract": "implement",
        "message": (
            f"---\ncontract: implement\nsource_ref: {source_ref}\n"
            "files_expected:\n- services/x.py\n---\nimpl"
        ),
    }
    base.update(overrides)
    return base


def _ledger_row(dispatch_id: str) -> dict[str, Any]:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, lease_key, record_json FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_ac_s2_1_lane_b_admits_with_minted_workspace(
    _mock_task: MagicMock,
    client: TestClient,
    worker_cfg: WorkerConfig,
) -> None:
    """AC-S2.1: lane='B' admits with minted dispatch_workspace."""
    resp = client.post("/api/v1/cursor/dispatch", json=_body(lane="B"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "admitted"
    row = _ledger_row("disp-s2")
    lease_key = row["lease_key"]
    assert lease_key is not None
    wt = lookup_dispatch_worktree(dispatch_id="disp-s2")
    assert wt is not None
    assert str(wt.worktree_path.resolve()) == lease_key
    req = CursorDispatchRequest(**_body(lane="B"))
    workspace = resolve_dispatch_workspace(
        req,
        worker_cfg,
        dispatch_workspace=Path(lease_key),
    )
    assert workspace.is_dir()


def test_ac_s2_1_falsifier_lane_b_without_mint_raises(worker_cfg: WorkerConfig) -> None:
    """AC-S2.1 falsifier: Lane-B wire without minted workspace still raises."""
    req = CursorDispatchRequest(
        thread_id="t",
        model="cursor/composer-2.5",
        dispatch_id="d",
        execution_id="e",
        message="x",
        lane="B",
    )
    with pytest.raises(ValueError, match="minted dispatch_workspace"):
        resolve_dispatch_workspace(req, worker_cfg)


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_ac_s2_2_two_concurrent_lane_b_distinct_lease_keys(
    _mock_task: MagicMock,
    client: TestClient,
) -> None:
    """AC-S2.2: two lane='B' dispatches on distinct threads get distinct lease keys."""
    first = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            dispatch_id="b-one",
            execution_id="exec-b-one",
            thread_id="6661",
            lane="B",
            source_ref="todo:b-one",
        ),
    )
    second = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            dispatch_id="b-two",
            execution_id="exec-b-two",
            thread_id="6662",
            lane="B",
            source_ref="todo:b-two",
        ),
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "admitted"
    assert second.json()["status"] == "admitted"
    key_one = _ledger_row("b-one")["lease_key"]
    key_two = _ledger_row("b-two")["lease_key"]
    assert key_one != key_two


def test_ac_s2_3_regime_directions(git_repo: Path) -> None:
    """AC-S2.3 / row-10 AC5–7: regime off/on, contract_regime, lane='A' opt-out."""
    req = CursorDispatchRequest(
        thread_id="t",
        model="cursor/composer-2.5",
        dispatch_id="d",
        execution_id="e",
        message="x",
    )
    files = ["services/a.py"]
    set_lane_b_regime(active=False)
    lane, _, reason = select_lane(
        req=req,
        regime_active=lane_b_regime_active(),
        source_repo=git_repo,
        files_expected=files,
        contract="implement",
    )
    assert lane == "A"
    assert reason == "opt_out"

    set_lane_b_regime(active=True)
    lane, _, reason = select_lane(
        req=req,
        regime_active=lane_b_regime_active(),
        source_repo=git_repo,
        files_expected=files,
        contract="implement",
    )
    assert lane == "B"
    assert reason == "contract_regime"

    lane, _, reason = select_lane(
        req=req,
        regime_active=lane_b_regime_active(),
        source_repo=git_repo,
        files_expected=files,
        contract="none",
    )
    assert lane == "B"
    assert reason == "contract_regime"

    req_a = req.model_copy(update={"lane": "A"})
    lane, _, reason = select_lane(
        req=req_a,
        regime_active=lane_b_regime_active(),
        source_repo=git_repo,
        files_expected=files,
        contract="implement",
    )
    assert lane == "A"
    assert reason == "opt_out"

    lane, _, reason = select_lane(
        req=req,
        regime_active=lane_b_regime_active(),
        source_repo=git_repo,
        files_expected=[],
        contract="implement",
    )
    assert lane == "A"
    assert reason == "opt_out"
    set_lane_b_regime(active=False)


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_ac_s2_4_regime_off_contention_stays_lane_a(
    _mock_task: MagicMock,
    client: TestClient,
) -> None:
    """AC-S2.4 / D1: regime off — second implement writer queues on Lane-A, not Lane-B."""
    assert lane_b_regime_active() is False
    first = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            dispatch_id="a-one", execution_id="exec-a-one", source_ref="todo:a-one"
        ),
    )
    second = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            dispatch_id="a-two", execution_id="exec-a-two", source_ref="todo:a-two"
        ),
    )
    assert first.status_code == 200
    assert second.status_code == 202
    assert second.json()["status"] == "queued"
    row = _ledger_row("a-two")
    record = json.loads(row["record_json"])
    assert record.get("lane") in (None, "A")
    assert lookup_dispatch_worktree(dispatch_id="a-two") is None


def test_ac_s2_5_scope_veto(git_repo: Path) -> None:
    """AC-S2.5: out-of-repo files_expected vetoes Lane-B when unset; refuses explicit B."""
    req = CursorDispatchRequest(
        thread_id="t",
        model="cursor/composer-2.5",
        dispatch_id="d",
        execution_id="e",
        message="x",
    )
    lane, _, _ = select_lane(
        req=req,
        regime_active=True,
        source_repo=git_repo,
        files_expected=["cortex://notes/x.md"],
    )
    assert lane == "A"

    lane, _, _ = select_lane(
        req=req,
        regime_active=False,
        source_repo=git_repo,
        files_expected=[],
    )
    assert lane == "A"

    req_b = req.model_copy(update={"lane": "B"})
    from services.git_integration_worker.cursor_sdk_lane_select import LaneScopeRefused

    with pytest.raises(LaneScopeRefused) as exc_info:
        select_lane(
            req=req_b,
            regime_active=False,
            source_repo=git_repo,
            files_expected=["cortex://notes/x.md"],
        )
    message = str(exc_info.value)
    assert "cortex://notes/x.md" in message
    assert "cortex:// reference" in message
    assert "files_expected:" in message  # front-matter override hint


def test_scope_refused_names_absolute_outside_repo_offender(git_repo: Path) -> None:
    """LaneScopeRefused (friction a:31774) names the offending path and why."""
    from services.git_integration_worker.cursor_sdk_lane_select import LaneScopeRefused

    req_b = CursorDispatchRequest(
        thread_id="t",
        model="cursor/composer-2.5",
        dispatch_id="d",
        execution_id="e",
        message="x",
        lane="B",
    )
    with pytest.raises(LaneScopeRefused) as exc_info:
        select_lane(
            req=req_b,
            regime_active=False,
            source_repo=git_repo,
            files_expected=["/mcp/code.py"],
        )
    message = str(exc_info.value)
    assert "/mcp/code.py" in message
    assert "absolute path not under source_repo" in message


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_ac_s2_6_read_only_lane_b_422(
    _mock_task: MagicMock, client: TestClient
) -> None:
    """read_only + lane='B' admits. CURSOR_LANE_B_READ_ONLY is gone.

    Contract stays ``none``: ``CURSOR_READONLY_IMPLEMENT_CONFLICT`` still
    refuses read_only implement.
    """
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            read_only=True,
            lane="B",
            handoff_contract="none",
            message="---\ncontract: none\n---\nread",
            dispatch_id="disp-ro-b",
            execution_id="exec-ro-b",
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["admitted"] is True
    assert CursorDispatchLedger.instance().read_read_only(dispatch_id="disp-ro-b") is True


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_lane_b_shared_master_lease_is_422(
    _mock_task: MagicMock,
    client: TestClient,
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selected Lane-B with a shared-master lease refuses; it does not relabel to A."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    repo = git_repo.resolve()

    from services.git_integration_worker.cursor_sdk_worktree import AdmitBindingResult

    def _shared_master_binding(**_kwargs: object) -> AdmitBindingResult:
        return AdmitBindingResult(
            workspace=repo,
            lease_key=str(repo),
            binding_kind="lane_a",
        )

    monkeypatch.setattr(route_mod, "resolve_admit_binding", _shared_master_binding)
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(lane="B", dispatch_id="b-missing", execution_id="exec-b-missing"),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "CURSOR_LANE_B_WORKTREE_MISSING"
    assert "materialized worktree" in body["message"]
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("b-missing",),
        ).fetchone()
    assert row is None


_LANE_A_FIX_HINT = (
    'Pass lane="B". Lane A is refused at admit. '
    "In-repo work and empty files_expected use a lane-B worktree. "
    "sdk_mode=plan on lane B is read-only and does not take the write lease. "
    "cortex:// paths and paths outside the repo remain 422 "
    "CURSOR_LANE_B_SCOPE_REFUSED."
)


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
@patch(
    "services.git_integration_worker.routes.cursor_sdk.emit_write_lease_acquired",
)
def test_plan_lane_b_admits_read_only_without_write_lease(
    mock_emit: MagicMock,
    _mock_task: MagicMock,
    client: TestClient,
) -> None:
    """AC1: sdk_mode=plan on lane B admits read-only and does not take the write lease."""
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            lane="B",
            handoff_contract="none",
            message="---\nsdk_mode: plan\n---\nplan body",
            dispatch_id="disp-plan-b",
            execution_id="exec-plan-b",
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["admitted"] is True
    ledger = CursorDispatchLedger.instance()
    read_only = ledger.read_read_only(dispatch_id="disp-plan-b")
    assert read_only is True
    from services.git_integration_worker.cursor_sdk_mode import (
        sdk_mode_from_record_json,
    )

    row = _ledger_row("disp-plan-b")
    sdk_mode = sdk_mode_from_record_json(row["record_json"])
    assert sdk_mode == "plan"
    mock_emit.assert_not_called()
    print(
        "AC1 receipt "
        f"admitted={body['admitted']} "
        f"read_only={int(read_only)} "
        f"sdk_mode={sdk_mode} "
        f"emit_write_lease_acquired_called={mock_emit.called}"
    )


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_explicit_lane_a_refused_with_fix_hint(
    _mock_task: MagicMock,
    client: TestClient,
) -> None:
    """AC2: wire lane=A is 422 CURSOR_LANE_A_REFUSED with the fix_hint sentence."""
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            lane="A",
            dispatch_id="disp-lane-a",
            execution_id="exec-lane-a",
        ),
    )
    assert resp.status_code == 422
    payload = resp.json()
    assert payload["code"] == "CURSOR_LANE_A_REFUSED"
    assert payload["data"]["fix_hint"] == _LANE_A_FIX_HINT
    print(
        json.dumps(
            {"code": payload["code"], "data": {"fix_hint": payload["data"]["fix_hint"]}}
        )
    )


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_nest_under_execution_uuid_is_422(
    _mock_task: MagicMock, client: TestClient
) -> None:
    """Breaks when nest_under is an execution_id UUID and admit still mints (503)."""
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            lane="B",
            nest_under="550e8400-e29b-41d4-a716-446655440000",
            dispatch_id="child-uuid-nest",
            execution_id="exec-child-uuid-nest",
            thread_id="6702",
            source_ref="todo:child-uuid-nest",
        ),
    )
    assert resp.status_code == 422
    payload = resp.json()
    assert payload["code"] == "nest_under_not_dispatch_id"
    hint = payload["data"]["fix_hint"]
    assert "dispatch_id" in hint.lower()
    assert "execution_id" in hint.lower()
    assert "12 hex chars, hyphen, 12" not in hint


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_nest_under_execution_uuid_maps_dispatch_id_in_fix_hint(
    _mock_task: MagicMock, client: TestClient, git_repo: Path
) -> None:
    """Breaks when execution_id nest_under omits the ledger dispatch_id from fix_hint."""
    from services.git_integration_worker.models.cursor_api import CursorDispatchResponse

    exec_uuid = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    parent_id = "parent-exec-map"
    ledger = CursorDispatchLedger.instance()
    parent_req = CursorDispatchRequest(
        thread_id="6703",
        model="cursor/composer-2.5",
        dispatch_id=parent_id,
        execution_id=exec_uuid,
        message="parent",
        lane="B",
    )
    ledger.admit(
        req=parent_req,
        fingerprint=ledger.fingerprint(parent_req),
        execution_id=exec_uuid,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=parent_id,
            thread_id="6703",
            model_id="composer-2.5",
        ),
        source_repo=str(git_repo.resolve()),
        lease_key=str(git_repo.resolve()),
        contract="implement",
        worker_instance="worker-a",
    )
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            lane="B",
            nest_under=exec_uuid,
            dispatch_id="child-exec-map",
            execution_id="exec-child-exec-map",
            thread_id="6704",
            source_ref="todo:child-exec-map",
        ),
    )
    assert resp.status_code == 422
    payload = resp.json()
    assert payload["code"] == "nest_under_not_dispatch_id"
    assert parent_id in payload["data"]["fix_hint"]


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_nest_under_uuid_shaped_dispatch_id_in_ledger_not_422(
    _mock_task: MagicMock, client: TestClient, git_repo: Path
) -> None:
    """Hop successors use bare uuid4 dispatch_ids; nest_under must not 422 them."""
    from services.git_integration_worker.models.cursor_api import CursorDispatchResponse

    hop_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    ledger = CursorDispatchLedger.instance()
    parent_req = CursorDispatchRequest(
        thread_id="6705",
        model="cursor/composer-2.5",
        dispatch_id=hop_uuid,
        execution_id="exec-hop-uuid",
        message="parent",
        lane="B",
    )
    ledger.admit(
        req=parent_req,
        fingerprint=ledger.fingerprint(parent_req),
        execution_id="exec-hop-uuid",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=hop_uuid,
            thread_id="6705",
            model_id="composer-2.5",
        ),
        source_repo=str(git_repo.resolve()),
        lease_key=str(git_repo.resolve()),
        contract="implement",
        worker_instance="worker-a",
    )
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            lane="B",
            nest_under=hop_uuid,
            dispatch_id="child-hop-uuid",
            execution_id="exec-child-hop-uuid",
            thread_id="6706",
            source_ref="todo:child-hop-uuid",
        ),
    )
    if resp.status_code == 422:
        assert resp.json().get("code") != "nest_under_not_dispatch_id"


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_explicit_lane_b_nest_under_lane_a_parent_is_422(
    _mock_task: MagicMock,
    client: TestClient,
    git_repo: Path,
) -> None:
    """Explicit lane=A is 422. A seeded Lane-A parent still makes a lane=B nest 422."""
    from services.git_integration_worker.cursor_sdk_workspace import lane_a_lease_key
    from services.git_integration_worker.models.cursor_api import CursorDispatchResponse

    parent = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            lane="A",
            dispatch_id="parent-a",
            execution_id="exec-parent-a",
            thread_id="6701",
            source_ref="todo:parent-a",
        ),
    )
    assert parent.status_code == 422
    assert parent.json()["code"] == "CURSOR_LANE_A_REFUSED"
    parent_req = CursorDispatchRequest(
        thread_id="6701",
        model="cursor/composer-2.5",
        dispatch_id="parent-a",
        execution_id="exec-parent-a",
        message="parent",
        lane="A",
        handoff_contract="implement",
    )
    ledger = CursorDispatchLedger.instance()
    ledger.admit(
        req=parent_req,
        fingerprint=ledger.fingerprint(parent_req),
        execution_id=parent_req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="parent-a",
            thread_id="6701",
            model_id="composer-2.5",
        ),
        source_repo=str(git_repo.resolve()),
        lease_key=lane_a_lease_key(git_repo),
        contract="implement",
        worker_instance="worker-a",
        read_only=False,
    )
    child = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            lane="B",
            nest_under="parent-a",
            dispatch_id="child-b-on-a",
            execution_id="exec-child-b-on-a",
            thread_id="6701",
            source_ref="todo:child-b-on-a",
        ),
    )
    assert child.status_code == 422
    assert child.json()["code"] == "CURSOR_LANE_B_WORKTREE_MISSING"


def test_nest_omitted_lane_inherits_parent_isolation_not_regime(git_repo: Path) -> None:
    """Regime ON must not label a shared-master nest as Lane-B."""
    parent = CursorDispatchRequest(
        thread_id="t",
        model="cursor/composer-2.5",
        dispatch_id="child",
        execution_id="e",
        message="x",
        nest_under="parent-a",
    )
    set_lane_b_regime(active=True)
    lane, _, reason = select_lane(
        req=parent,
        regime_active=True,
        source_repo=git_repo,
        files_expected=["services/a.py"],
        contract="implement",
        parent_isolated=False,
    )
    assert lane == "A"
    assert reason == "nest_inherit"

    lane_b, _, reason_b = select_lane(
        req=parent,
        regime_active=False,
        source_repo=git_repo,
        files_expected=["services/a.py"],
        contract="implement",
        parent_isolated=True,
    )
    assert lane_b == "B"
    assert reason_b == "nest_inherit"
    set_lane_b_regime(active=False)


def test_resume_omitted_lane_inherits_parent_isolation(git_repo: Path) -> None:
    req = CursorDispatchRequest(
        thread_id="t",
        model="cursor/composer-2.5",
        dispatch_id="child",
        execution_id="e",
        message="x",
        resume_of="parent-a",
    )
    lane, _, reason = select_lane(
        req=req,
        regime_active=True,
        source_repo=git_repo,
        files_expected=["services/a.py"],
        contract="implement",
        parent_isolated=False,
    )
    assert lane == "A"
    assert reason == "nest_inherit"


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_ac_s2_7_nest_under_lane_b_inherits_parent_tree(
    _mock_task: MagicMock,
    client: TestClient,
    worker_cfg: WorkerConfig,
    git_repo: Path,
) -> None:
    """AC-S2.7: nest_under Lane-B parent inherits lease_key; no second mint."""
    from services.git_integration_worker.cursor_sdk_worktree import (
        resolve_admit_binding,
    )
    from services.git_integration_worker.models.cursor_api import CursorDispatchResponse

    parent_req = CursorDispatchRequest(
        thread_id="6661",
        model="cursor/composer-2.5",
        dispatch_id="parent-b",
        execution_id="exec-parent-b",
        message="parent",
        lane="B",
    )
    parent = resolve_admit_binding(
        req=parent_req,
        source_repo=git_repo,
        hub=git_repo,
        worktree_root=worker_cfg.worktree_root,
        dispatch_workspace_default=worker_cfg.dispatch_workspace,
        lane="B",
    )
    parent_ws = parent.workspace
    parent_key = parent.lease_key
    ledger = CursorDispatchLedger.instance()
    ledger.admit(
        req=parent_req,
        fingerprint=ledger.fingerprint(parent_req),
        execution_id=parent_req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="parent-b",
            thread_id="6661",
            model_id="composer-2.5",
        ),
        source_repo=str(git_repo.resolve()),
        lease_key=parent_key,
        contract="implement",
        worker_instance="worker-a",
    )
    child_req = CursorDispatchRequest(
        thread_id="6661",
        model="cursor/composer-2.5",
        dispatch_id="child-b",
        execution_id="exec-child-b",
        message="child",
        nest_under="parent-b",
    )
    child = resolve_admit_binding(
        req=child_req,
        source_repo=git_repo,
        hub=git_repo,
        worktree_root=worker_cfg.worktree_root,
        dispatch_workspace_default=worker_cfg.dispatch_workspace,
        lane="A",
    )
    assert child.binding_kind == "nested"
    assert child.workspace == parent_ws
    assert child.lease_key == parent_key
    assert lookup_dispatch_worktree(dispatch_id="child-b") is None


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_ac_s2_8_post_mint_rejection_rolls_back(
    _mock_task: MagicMock,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-S2.8: post-mint admit rejection prunes minted tree + emits rollback."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    events: list[dict[str, str]] = []
    monkeypatch.setattr(
        route_mod,
        "emit_sdk_lane_b_mint_rolled_back",
        lambda **kwargs: events.append(kwargs),
    )

    def _raise_conflict(*_a: object, **_k: object) -> None:
        raise DispatchConflict("fingerprint mismatch")

    monkeypatch.setattr(CursorDispatchLedger, "admit", _raise_conflict)

    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(dispatch_id="rollback-b", execution_id="exec-rollback-b", lane="B"),
    )
    assert resp.status_code == 409
    assert lookup_dispatch_worktree(dispatch_id="rollback-b") is None
    assert events and events[0]["reason"] == "dispatch_conflict"


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_ac_s2_9_worktree_isolated_deprecated_still_lane_b(
    _mock_task: MagicMock,
    client: TestClient,
) -> None:
    """AC-S2.9: worktree_isolated=True still resolves to Lane-B."""
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            dispatch_id="dep-b",
            execution_id="exec-dep-b",
            worktree_isolated=True,
            lane=None,
        ),
    )
    assert resp.status_code == 200
    record = json.loads(_ledger_row("dep-b")["record_json"])
    assert record.get("lane") == "B"
    assert lookup_dispatch_worktree(dispatch_id="dep-b") is not None


def test_lb1_regime_default_on() -> None:
    """LB-1 row-10: fleet regime default ON when DB row missing."""
    from services.git_integration_worker.cursor_dispatch_ledger import _connect
    from services.git_integration_worker.cursor_sdk_lane_regime import (
        _REGIME_KEY,
        ensure_regime_schema,
    )

    with _connect() as conn:
        ensure_regime_schema(conn)
        conn.execute("DELETE FROM cursor_sdk_regime WHERE key=?", (_REGIME_KEY,))
    assert lane_b_regime_active() is True
    assert default_write_path_is_lane_a() is False
    set_lane_b_regime(active=False)


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_row10_ac8_sdk_lane_selected_carries_contract(
    _mock_task: MagicMock,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row-10 AC8: sdk.lane.selected carries contract + selecting_predicate."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    captured: list[dict[str, object]] = []

    def _capture(**kwargs: object) -> None:
        captured.append(dict(kwargs))

    set_lane_b_regime(active=True)
    monkeypatch.setattr(route_mod, "emit_sdk_lane_selected", _capture)

    body = _body()
    body["message"] = (
        f"---\ncontract: implement\nsource_ref: {body.get('source_ref', 'todo:s2')}\n---\n"
        "<scope>\nFiles expected:\n- `services/x.py`\n</scope>\nimpl"
    )
    resp = client.post("/api/v1/cursor/dispatch", json=body)
    assert resp.status_code == 200
    assert captured
    event = captured[0]
    assert event["contract"] == "implement"
    assert event["lane"] == "B"
    assert event["reason"] == "contract_regime"
    assert "contract=implement" in str(event["selecting_predicate"])
    assert event["regime_active"] is True
    set_lane_b_regime(active=False)


def test_row10_d4_non_implement_contract_keeps_regime_eligibility(
    git_repo: Path,
) -> None:
    """Row-10 D4: consult with scoped files still selects Lane-B when regime ON."""
    req = CursorDispatchRequest(
        thread_id="t",
        model="cursor/composer-2.5",
        dispatch_id="d",
        execution_id="e",
        message="x",
    )
    set_lane_b_regime(active=True)
    lane, _, reason = select_lane(
        req=req,
        regime_active=True,
        source_repo=git_repo,
        files_expected=["services/a.py"],
        contract="consult",
    )
    assert lane == "B"
    assert reason == "regime"
    set_lane_b_regime(active=False)


def test_auto_regime_empty_scope_pure_mechanical(git_repo: Path) -> None:
    req = CursorDispatchRequest(
        thread_id="t-auto",
        model="cursor/composer-2.5",
        dispatch_id="auto-belt",
        execution_id="exec-auto-belt",
        message="x",
        admitted_via="cursor-auto",
    )
    lane, _, reason = select_lane(
        req=req,
        regime_active=True,
        source_repo=git_repo,
        files_expected=[],
        contract="pure-mechanical",
    )
    assert lane == "B"
    assert reason == "auto_regime"


def test_auto_residual_empty_scope_stays_a(git_repo: Path) -> None:
    req = CursorDispatchRequest(
        thread_id="t-auto",
        model="cursor/composer-2.5",
        dispatch_id="auto-a",
        execution_id="exec-auto-a",
        message="x",
        admitted_via="cursor-auto",
    )
    lane, _, reason = select_lane(
        req=req,
        regime_active=True,
        source_repo=git_repo,
        files_expected=[],
        contract="none",
    )
    assert lane == "A"
    assert reason == "opt_out"


def test_scope_refused_retries_named_a(
    git_repo: Path, client: TestClient
) -> None:
    """select_lane still returns A for explicit A. HTTP admit refuses that wire lane."""
    from services.git_integration_worker.cursor_sdk_lane_select import LaneScopeRefused

    req = CursorDispatchRequest(
        thread_id="t",
        model="cursor/composer-2.5",
        dispatch_id="scope-b",
        execution_id="exec-scope-b",
        message="x",
        lane="B",
    )
    with pytest.raises(LaneScopeRefused):
        select_lane(
            req=req,
            regime_active=True,
            source_repo=git_repo,
            files_expected=["workspaces://other-repo/foo.py"],
            contract="implement",
        )
    retry = CursorDispatchRequest(
        thread_id="t",
        model="cursor/composer-2.5",
        dispatch_id="scope-a",
        execution_id="exec-scope-a",
        message="x",
        lane="A",
    )
    lane, _, reason = select_lane(
        req=retry,
        regime_active=True,
        source_repo=git_repo,
        files_expected=["workspaces://other-repo/foo.py"],
        contract="implement",
    )
    assert lane == "A"
    assert reason == "opt_out"
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(
            lane="A",
            dispatch_id="scope-a-http",
            execution_id="exec-scope-a-http",
            message=(
                "---\ncontract: implement\nfiles_expected:\n"
                "- workspaces://other-repo/foo.py\n---\n"
            ),
        ),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "CURSOR_LANE_A_REFUSED"
