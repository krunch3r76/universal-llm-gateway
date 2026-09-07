"""S1b worktree mint, prune-on-terminal, and orphan reaper."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.cursor_sdk_worktree import (
    WorktreeMintError,
    maybe_prune_worktree_on_terminal,
    mint_dispatch_worktree,
    reap_orphan_worktrees,
    resolve_admit_binding,
    resolve_master_branch_point,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    return repo


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


def test_s1b_mint_pins_resolved_commit(source_repo: Path, tmp_path: Path) -> None:
    """Mint uses an explicitly resolved branch point, not implicit tip sampling."""
    worktree_root = tmp_path / "worktrees"
    tip = resolve_master_branch_point(source_repo)
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="mint-a",
        branch_point=tip,
    )
    head = _git("rev-parse", "HEAD", cwd=wt).stdout.strip()
    assert head == tip
    assert wt.is_dir()
    slug = source_repo.resolve().name
    assert wt.parent == (worktree_root / slug).resolve()


def test_s1b_lane_b_resolve_admit_binding_mints(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC1: Lane-B admit binding mints under worktree_root."""
    worktree_root = tmp_path / "worktrees"
    req = _req(dispatch_id="lane-b-1", worktree_isolated=True)
    binding = resolve_admit_binding(
        req=req,
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    assert binding.binding_kind == "minted"
    assert binding.workspace.is_dir()
    assert str(binding.workspace.resolve()) == binding.lease_key
    assert binding.workspace.relative_to(worktree_root.resolve())


def test_s1b_prune_on_terminal(source_repo: Path, tmp_path: Path) -> None:
    """Lane trees outlive dispatches: terminal does not prune the worktree."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "prune-me"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    assert wt.is_dir()
    result = maybe_prune_worktree_on_terminal(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    assert not result.pruned
    assert wt.is_dir()


def test_s1b_prune_retains_branch_when_dirty(source_repo: Path, tmp_path: Path) -> None:
    """S3: explicit dirty prune retains the unmerged lane branch."""
    from services.git_integration_worker.cursor_sdk_worktree_prune import (
        prune_dispatch_worktree,
    )

    worktree_root = tmp_path / "worktrees"
    dispatch_id = "prune-dirty"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    (wt / "dirty.py").write_text("x\n", encoding="utf-8")
    branch = f"cursor-sdk/lane-{dispatch_id}"
    result = prune_dispatch_worktree(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    assert result.pruned
    assert result.branch_retained
    assert result.salvaged
    assert branch in _git("branch", "--list", branch, cwd=source_repo).stdout


def test_s1b_reaper_retains_standing_lane_after_terminal(
    source_repo: Path, tmp_path: Path
) -> None:
    """Registered empty lane is idle after terminal, not an orphan."""
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        lookup_lane_worktree,
    )

    worktree_root = tmp_path / "worktrees"
    dispatch_id = "orphan-a"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/lane-{dispatch_id}"
    ledger = CursorDispatchLedger.instance()
    ledger.admit(
        req=_req(dispatch_id=dispatch_id),
        fingerprint="fp",
        execution_id="exec",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id="t1",
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=str(wt.resolve()),
        contract="consult",
        worker_instance="worker-a",
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    assert wt.is_dir()
    removed = reap_orphan_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )
    assert removed.reaped == 0
    assert wt.is_dir()
    assert branch in _git("branch", "--list", branch, cwd=source_repo).stdout
    assert lookup_lane_worktree(thread_id=dispatch_id, source_repo=source_repo) is not None


def test_s1b_route_wires_resolve_admit_binding() -> None:
    """AC1: cursor_sdk route resolves Lane-B workspace before ledger admit."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source = Path(route_mod.__file__).read_text(encoding="utf-8")
    assert "resolve_admit_binding" in source


def test_s1b_mint_reattaches_existing_lane_branch(
    source_repo: Path, tmp_path: Path
) -> None:
    """Branch exists and worktree dir is gone → attach without ``-b`` (7240 class)."""
    worktree_root = tmp_path / "worktrees"
    thread_id = "7240-sim"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="reattach-1",
        thread_id=thread_id,
    )
    (wt / "lane_work.py").write_text("kept\n", encoding="utf-8")
    _git("add", "lane_work.py", cwd=wt)
    _git("commit", "-m", "lane work", cwd=wt)
    tip = _git("rev-parse", "HEAD", cwd=wt).stdout.strip()
    branch = f"cursor-sdk/lane-{thread_id}"
    _git("worktree", "remove", "--force", str(wt), cwd=source_repo)
    assert not wt.exists()
    assert branch in _git("branch", "--list", branch, cwd=source_repo).stdout

    wt2 = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="reattach-2",
        thread_id=thread_id,
    )
    assert wt2.is_dir()
    assert (wt2 / "lane_work.py").read_text(encoding="utf-8") == "kept\n"
    assert _git("rev-parse", "HEAD", cwd=wt2).stdout.strip() == tip
    assert _git("branch", "--show-current", cwd=wt2).stdout.strip() == branch


def test_s1b_admit_reattaches_when_registry_dir_gone(
    source_repo: Path, tmp_path: Path
) -> None:
    """Registry row pointing at a missing dir still remints by attaching the branch."""
    worktree_root = tmp_path / "worktrees"
    first = resolve_admit_binding(
        req=_req(dispatch_id="gone-dir-1", thread_id="t-gone", worktree_isolated=True),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    (first.workspace / "kept.txt").write_text("visible\n", encoding="utf-8")
    _git("add", "kept.txt", cwd=first.workspace)
    _git("commit", "-m", "keep", cwd=first.workspace)
    _git("worktree", "remove", "--force", str(first.workspace), cwd=source_repo)
    assert not first.workspace.exists()

    second = resolve_admit_binding(
        req=_req(dispatch_id="gone-dir-2", thread_id="t-gone", worktree_isolated=True),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    assert second.workspace.is_dir()
    assert (second.workspace / "kept.txt").read_text(encoding="utf-8") == "visible\n"
    assert str(second.workspace.resolve()) == second.lease_key


def test_worktree_mint_error_defaults_not_retryable() -> None:
    """Permanent mint collisions are not retryable; lock exhaustion is."""
    permanent = WorktreeMintError("fatal: a branch named 'x' already exists")
    assert permanent.retryable is False
    transient = WorktreeMintError("index.lock: File exists", retryable=True)
    assert transient.retryable is True


def test_s1b_route_mint_failure_uses_exc_retryable() -> None:
    """Route must not hardcode retryable=True on every WorktreeMintError."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source = Path(route_mod.__file__).read_text(encoding="utf-8")
    assert "retryable=getattr(exc, \"retryable\", False)" in source
    assert "except WorktreeMintError as exc:" in source


def test_s1b_lane_a_binding_unchanged(source_repo: Path, tmp_path: Path) -> None:
    """Lane-A default path still uses shared dispatch_workspace + source_repo lease."""
    shared = tmp_path / "shared"
    shared.mkdir()
    req = _req(worktree_isolated=False)
    binding = resolve_admit_binding(
        req=req,
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace_default=shared,
        lane="A",
    )
    assert binding.binding_kind == "lane_a"
    assert binding.workspace == shared
    assert binding.lease_key == str(source_repo.resolve())


def test_ac_b_1_discharge_scoped_by_source_repo(
    source_repo: Path,
    tmp_path: Path,
) -> None:
    """AC-B-1: ``_record_for_branch`` resolves per ``source_repo``."""
    from services.git_integration_worker.cursor_sdk_branch_unpin import (
        _record_for_branch,
    )
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        register_lane_worktree,
    )

    worktree_root = tmp_path / "worktrees"
    tip = resolve_master_branch_point(source_repo)
    other = tmp_path / "other-repo"
    other.mkdir()
    _git("init", "-b", "master", cwd=other)
    _git("config", "user.email", "t@example.com", cwd=other)
    _git("config", "user.name", "t", cwd=other)
    (other / "README.md").write_text("x\n", encoding="utf-8")
    _git("add", "README.md", cwd=other)
    _git("commit", "-m", "seed", cwd=other)
    branch = "cursor-sdk/lane-collision"
    wt_a = worktree_root / source_repo.name / "lane-collision"
    wt_b = worktree_root / other.name / "lane-collision"
    wt_a.mkdir(parents=True)
    wt_b.mkdir(parents=True)
    register_lane_worktree(
        source_repo=source_repo,
        thread_id="collision",
        worktree_path=wt_a,
        branch_name=branch,
        branch_point=tip,
    )
    register_lane_worktree(
        source_repo=other,
        thread_id="collision",
        worktree_path=wt_b,
        branch_name=branch,
        branch_point=tip,
    )
    rec_a = _record_for_branch(source_repo=source_repo, branch_name=branch)
    rec_b = _record_for_branch(source_repo=other, branch_name=branch)
    assert rec_a is not None and rec_a.worktree_path.resolve() == wt_a.resolve()
    assert rec_b is not None and rec_b.worktree_path.resolve() == wt_b.resolve()


def test_ac_b_4_mint_path_matches_git_worktree_list(
    source_repo: Path,
    tmp_path: Path,
) -> None:
    """AC-B-4: registry path equals ``git worktree list`` after mint."""
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        lookup_lane_worktree,
    )

    worktree_root = tmp_path / "worktrees"
    thread_id = "path-check"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="disp-path",
        thread_id=thread_id,
    )
    record = lookup_lane_worktree(thread_id=thread_id, source_repo=source_repo)
    assert record is not None
    assert record.worktree_path.resolve() == wt.resolve()
    listed = {
        line.removeprefix("worktree ").strip()
        for line in _git("worktree", "list", "--porcelain", cwd=source_repo)
        .stdout.splitlines()
        if line.startswith("worktree ")
    }
    assert str(wt.resolve()) in listed


def test_ac_b_3_registry_register_emits(
    source_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-B-3: register emits ``sdk.lane_b.registry_registered``."""
    emitted: list[dict] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events."
        "emit_sdk_lane_b_registry_registered",
        lambda **kwargs: emitted.append(kwargs),
    )
    worktree_root = tmp_path / "worktrees"
    mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="emit-reg",
        thread_id="emit-reg",
    )
    assert emitted
    assert emitted[0]["trigger"] == "register"
    assert emitted[0]["source_repo"] == str(source_repo.resolve())


def test_ac_b_2_worktree_removed_emit_carries_source_repo(
    source_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-B-2: removal emit carries ``source_repo`` on all paths (unpin sample)."""
    from services.git_integration_worker.cursor_sdk_branch_unpin import (
        unpin_registered_lane_worktree,
    )
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        register_lane_worktree,
    )

    tip = _git("rev-parse", "HEAD", cwd=source_repo).stdout.strip()
    branch = "cursor-sdk/lane-ac-b-2"
    _git("branch", branch, tip, cwd=source_repo)
    wt = source_repo.parent / "lane-ac-b-2"
    wt.mkdir()
    _git("worktree", "add", str(wt), branch, cwd=source_repo)
    register_lane_worktree(
        source_repo=source_repo,
        thread_id="ac-b-2",
        worktree_path=wt,
        branch_name=branch,
        branch_point=tip,
    )
    removed: list[dict] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_unpin."
        "emit_sdk_lane_b_worktree_removed",
        lambda **kwargs: removed.append(kwargs),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_unpin."
        "worktree_held_by_live_bridge",
        lambda **kwargs: None,
    )
    result = unpin_registered_lane_worktree(repo=source_repo, branch_name=branch)
    assert result.unpinned is True
    assert removed
    assert removed[0]["source_repo"] == str(source_repo.resolve())
    assert removed[0]["trigger"] == "unpin"


_LEGACY_LANE_DDL = """
CREATE TABLE cursor_sdk_lane_worktrees (
    thread_id TEXT PRIMARY KEY,
    worktree_path TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    branch_point TEXT NOT NULL,
    minted_at TEXT NOT NULL,
    last_dispatch_id TEXT,
    salvage_refusal_count INTEGER NOT NULL DEFAULT 0,
    quarantined_at TEXT
);
"""


def _run_legacy_migration(conn) -> None:
    import services.git_integration_worker.cursor_sdk_worktree_registry as reg

    reg._SCHEMA_MIGRATED = False
    reg.ensure_worktree_schema(conn)
    conn.commit()


def test_migrate_lane_worktrees_pk_empty_legacy_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B1: empty legacy PK table migrates without commit/rollback crash."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    CursorDispatchLedger.instance()

    with _connect() as conn:
        conn.execute("DROP TABLE IF EXISTS cursor_sdk_lane_worktrees")
        conn.executescript(_LEGACY_LANE_DDL)
        conn.commit()
        _run_legacy_migration(conn)
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(cursor_sdk_lane_worktrees)")
        }
        assert "source_repo" in cols
        assert (
            conn.execute("SELECT COUNT(*) FROM cursor_sdk_lane_worktrees").fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE name='cursor_sdk_lane_worktrees_new'"
            ).fetchone()
            is None
        )


def test_migrate_lane_worktrees_pk_populated_legacy_table(
    source_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B1: legacy rows resolve ``source_repo`` via ``last_dispatch_id`` join."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    CursorDispatchLedger.instance()
    repo_str = str(source_repo.resolve())
    wt_path = tmp_path / "lane-legacy"
    wt_path.mkdir()

    with _connect() as conn:
        conn.execute("DROP TABLE IF EXISTS cursor_sdk_lane_worktrees")
        conn.executescript(_LEGACY_LANE_DDL)
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, resolved_model, status, "
            "record_json, source_repo) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "disp-mig",
                "fp",
                "legacy-t",
                "cursor/composer-2.5",
                "completed",
                "{}",
                repo_str,
            ),
        )
        conn.execute(
            "INSERT INTO cursor_sdk_lane_worktrees VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-t",
                str(wt_path),
                "cursor-sdk/lane-legacy-t",
                "abc123",
                "2026-01-01T00:00:00+00:00",
                "disp-mig",
                0,
                None,
            ),
        )
        conn.commit()
        _run_legacy_migration(conn)
        row = conn.execute(
            "SELECT source_repo, thread_id FROM cursor_sdk_lane_worktrees"
        ).fetchone()
        assert row is not None
        assert row["source_repo"] == repo_str
        assert row["thread_id"] == "legacy-t"


def test_migrate_lane_worktrees_pk_retry_after_orphan_new_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B1: orphan ``_new`` table from a failed attempt does not poison retry."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    CursorDispatchLedger.instance()

    with _connect() as conn:
        conn.execute("DROP TABLE IF EXISTS cursor_sdk_lane_worktrees")
        conn.executescript(_LEGACY_LANE_DDL)
        conn.execute("CREATE TABLE cursor_sdk_lane_worktrees_new (thread_id TEXT)")
        conn.commit()
        _run_legacy_migration(conn)
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(cursor_sdk_lane_worktrees)")
        }
        assert "source_repo" in cols

