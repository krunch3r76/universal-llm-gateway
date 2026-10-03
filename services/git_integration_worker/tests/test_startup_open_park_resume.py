"""Boot reconcile: open park_for_restart survives reap and admits a resume child."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_park_ledger import mark_parked
from services.git_integration_worker.cursor_sdk_park_resume import (
    ADMITTED_VIA_PARK_RESUME,
)
from services.git_integration_worker.cursor_sdk_worktree import mint_dispatch_worktree
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.routes import cursor_sdk as route_mod


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(tmp_path / "homes"))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture
def _admit_stubs(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    from services.git_integration_worker.cursor_sdk_context import (
        CursorApiKeyResolution,
    )

    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"setting_sources": ["user"], "mcp_server": "vortex"},
    )
    monkeypatch.setattr(
        route_mod,
        "resolve_cursor_api_key",
        lambda *_a, **_k: CursorApiKeyResolution(provenance="env:CURSOR_API_KEY"),
    )
    monkeypatch.setattr(
        route_mod, "capture_wt_baseline_with_hashes", lambda *_a, **_k: {"files": {}}
    )
    spawned = MagicMock(return_value=MagicMock(done=lambda: False))
    monkeypatch.setattr(WorkAdmissionController, "create_tracked_task", spawned)
    return spawned


def _git_repo(path: Path) -> Path:
    import subprocess

    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "master"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"], cwd=path, check=True, capture_output=True
    )
    return path


@pytest.mark.asyncio
async def test_startup_reconcile_resumes_open_park_and_releases_work_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _admit_stubs: MagicMock
) -> None:
    """a:37673: reap-then-resume of an already-parked row, not a running orphan."""
    source_repo = _git_repo(tmp_path / "repo")
    worktree_root = tmp_path / "worktrees"
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(source_repo))
    monkeypatch.setenv("GIT_INTEGRATION_WORKTREE_ROOT", str(worktree_root))
    monkeypatch.setenv("GIT_INTEGRATION_DISPATCH_WORKSPACE", str(tmp_path / "projects"))
    dispatch_id = "park-boot-e2e"
    thread_id = "37673"
    work_key = "todo:37673-open-park"
    sat = _git_repo(tmp_path / "projects" / "cryptax")
    roster = source_repo / "cursor-plugins" / "ulg-ecosystem"
    roster.mkdir(parents=True)
    (roster / "SATELLITES.txt").write_text("cryptax\n", encoding="utf-8")
    wt = mint_dispatch_worktree(
        source_repo=sat,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        caller_agent="cursor",
        message="parked packet",
        worktree_isolated=True,
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
        source_repo=str(sat.resolve()),
        lease_key=str(wt.resolve()),
        contract="implement",
        worker_instance="worker-a",
        work_key=work_key,
        source_ref=work_key,
        identity_class="declared",
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    store = tmp_path / f"store-{dispatch_id}"
    store.mkdir()
    (store / "index.db").write_text("x", encoding="utf-8")
    ledger.record_state_root(dispatch_id=dispatch_id, state_root=str(store))
    ledger.record_sdk_identity(
        dispatch_id=dispatch_id, agent_id=f"agent-{dispatch_id}", run_id="r"
    )
    ledger.merge_record_json(dispatch_id=dispatch_id, patch={"workspace": "cryptax"})
    mark_parked(
        dispatch_id=dispatch_id,
        intent_id="intent-37673",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="2026-10-03T00:00:00Z",
        method="run_cancel",
        tool_call_count=0,
        last_tool_calls=[],
        sidecar_uri=None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_orphan.live_bridge_occupancy",
        lambda: [],
    )
    monkeypatch.setattr(route_mod, "prune_stale_dispatch_homes", lambda: 0)
    monkeypatch.setattr(
        route_mod,
        "reap_orphan_bridge_os",
        lambda *_a, **_k: SimpleNamespace(kill_failed=False, bridge_aborted=False),
    )
    monkeypatch.setattr(
        route_mod, "release_or_restore_for_child", AsyncMock(return_value="released")
    )
    monkeypatch.setattr(
        route_mod, "_promote_queued_for_lease", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        route_mod, "_resume_await_reply_rows", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_park_resume.CursorBusClient",
        lambda: SimpleNamespace(
            reply=AsyncMock(
                return_value=SimpleNamespace(status_code=201, body={"turn_number": 1})
            ),
            terminate_dispatch=AsyncMock(
                return_value=SimpleNamespace(status_code=200, body={})
            ),
        ),
    )

    controller = WorkAdmissionController(
        ledger=ledger,
        worker_id="boot-e2e",
        pid=0,
        worker_started_at=datetime.now(UTC).isoformat(),
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            worker_config=load_config(),
            admission_controller=controller,
            worker_version="test-37673",
        )
    )

    await route_mod.startup_ledger_reconcile(app)

    assert wt.is_dir()
    with ledger._connect() as conn:
        parent = conn.execute(
            "SELECT park_resumed_by, work_key FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        child = conn.execute(
            "SELECT dispatch_id, resume_of, work_key, record_json, status "
            "FROM cursor_sdk_dispatches WHERE resume_of=?",
            (dispatch_id,),
        ).fetchone()
    assert parent["park_resumed_by"] == child["dispatch_id"]
    assert child["work_key"] == work_key
    assert ADMITTED_VIA_PARK_RESUME in (child["record_json"] or "")
    assert "cryptax" in (child["record_json"] or "")
    assert sat.is_dir()
    ledger.mark_terminal(
        dispatch_id=child["dispatch_id"], terminal_status="completed"
    )
    peer = CursorDispatchRequest(
        thread_id="37673-peer",
        model="cursor/composer-2.5",
        dispatch_id="peer-after-resume",
        execution_id="exec-peer",
        message="key should be free",
    )
    peer_resp = ledger.admit(
        req=peer,
        fingerprint="fp-peer",
        execution_id="exec-peer",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="peer-after-resume",
            thread_id="37673-peer",
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=str(tmp_path / "peer-lease"),
        contract="implement",
        worker_instance="worker-a",
        work_key=work_key,
        source_ref=work_key,
        identity_class="declared",
    )
    assert peer_resp is None or peer_resp.admitted
    with ledger._connect() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                ("peer-after-resume",),
            ).fetchone()
            is not None
        )
