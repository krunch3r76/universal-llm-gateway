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
        lambda _repo: {"setting_sources": ["projectSettings"]},
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
        contract="light-bounded",
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

    with pytest.raises(LaneScopeRefused):
        select_lane(
            req=req_b,
            regime_active=False,
            source_repo=git_repo,
            files_expected=["cortex://notes/x.md"],
        )


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_ac_s2_6_read_only_lane_b_422(
    _mock_task: MagicMock, client: TestClient
) -> None:
    """AC-S2.6: read_only + lane='B' ⇒ 422 CURSOR_LANE_B_READ_ONLY."""
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_body(read_only=True, lane="B"),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "CURSOR_LANE_B_READ_ONLY"


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

    def _shared_master_binding(**_kwargs: object) -> tuple[Path, str]:
        return repo, str(repo)

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


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_explicit_lane_b_nest_under_lane_a_parent_is_422(
    _mock_task: MagicMock,
    client: TestClient,
) -> None:
    """Explicit lane=B under a Lane-A parent inherits shared master → 422."""
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
    assert parent.status_code == 200
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
    parent_ws, parent_key = resolve_admit_binding(
        req=parent_req,
        source_repo=git_repo,
        worktree_root=worker_cfg.worktree_root,
        dispatch_workspace_default=worker_cfg.dispatch_workspace,
        lane="B",
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
    child_ws, child_key = resolve_admit_binding(
        req=child_req,
        source_repo=git_repo,
        worktree_root=worker_cfg.worktree_root,
        dispatch_workspace_default=worker_cfg.dispatch_workspace,
        lane="A",
    )
    assert child_ws == parent_ws
    assert child_key == parent_key
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


def test_auto_light_bounded_empty_scope_stays_a(git_repo: Path) -> None:
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
        contract="light-bounded",
    )
    assert lane == "A"
    assert reason == "opt_out"


def test_scope_refused_retries_named_a(git_repo: Path) -> None:
    """CURSOR_LANE_B_SCOPE_REFUSED retry uses explicit lane=A, not omit."""
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
