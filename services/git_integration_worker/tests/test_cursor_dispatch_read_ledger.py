"""Work-key ledger projection and 5 KiB budget tests."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_dispatch_read import (
    _BUDGET_BYTES,
    _compact_json_bytes,
    _enforce_5kb_budget,
    work_key_ledger_projection,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    _SIDECAR_DIR,
    STRUCTURED_CLOSEOUT_FULL_HEADING,
)
from services.git_integration_worker.routes.cursor_claim import router as claim_router

pytestmark = pytest.mark.offline

_KEY = "todo:ledger-fixture"


def _ledger_check_predicate(payload: dict) -> bool:
    dispatches = payload.get("dispatches") or []
    if not dispatches:
        return False
    land = dispatches[0].get("land") or {}
    budget = payload.get("budget") or {}
    return (
        land.get("landed_now") is True
        and int(budget.get("actual_bytes") or 0) <= 5120
    )


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _insert_dispatch(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    work_key: str = _KEY,
    source_repo: Path | None = None,
    record_json: str = "{}",
    status: str = "completed",
    queued_at: str | None = None,
    started_at: str | None = None,
    terminal_at: str | None = None,
    resolved_model: str = "cursor/grok-4.7",
    contract: str = "freeform",
) -> None:
    stamp = queued_at or _now_iso()
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            "message_present, status, record_json, contract, work_key, queued_at, "
            "started_at, terminal_at, terminal_status, source_repo) "
            "VALUES (?, ?, 't1', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dispatch_id,
                dispatch_id,
                f"exec-{dispatch_id}",
                resolved_model,
                status,
                record_json,
                contract,
                work_key,
                stamp,
                started_at or stamp,
                terminal_at or stamp,
                status if status in ("completed", "failed", "cancelled") else None,
                str(source_repo) if source_repo else None,
            ),
        )


def _write_sidecar(
    source_repo: Path,
    dispatch_id: str,
    structured: dict,
) -> None:
    path = source_repo / _SIDECAR_DIR / f"{dispatch_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(structured, separators=(",", ":"), ensure_ascii=False)
    path.write_text(f"{STRUCTURED_CLOSEOUT_FULL_HEADING}\n\n{body}\n", encoding="utf-8")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _init_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "master", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )


def _commit_file(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", name],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", name],
        check=True,
        capture_output=True,
    )
    return _git(repo, "rev-parse", "HEAD")


def test_ledger_check_predicate_true_and_false(tmp_path: Path) -> None:
    ledger = CursorDispatchLedger.instance()
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    master_sha = _commit_file(repo, "on-master.txt", "on master\n")
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "-b", "side"],
        check=True,
        capture_output=True,
    )
    side_sha = _commit_file(repo, "side.txt", "side\n")
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "master"],
        check=True,
        capture_output=True,
    )
    structured_ok = {
        "head_sha": master_sha,
        "landed": True,
        "commits_ahead": 1,
        "status": "complete",
    }
    _insert_dispatch(
        ledger,
        dispatch_id="disp-check-ok",
        source_repo=repo,
        queued_at=_now_iso(),
    )
    _write_sidecar(repo, "disp-check-ok", structured_ok)
    ok_payload = work_key_ledger_projection(_KEY, ledger=ledger)
    assert _ledger_check_predicate(ok_payload) is True

    _insert_dispatch(
        ledger,
        dispatch_id="disp-check-bad",
        source_repo=repo,
        queued_at=(datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
    )
    _write_sidecar(
        repo,
        "disp-check-bad",
        {"head_sha": side_sha, "landed": False, "status": "partial"},
    )
    bad_payload = work_key_ledger_projection(_KEY, limit=1, ledger=ledger)
    assert _ledger_check_predicate(bad_payload) is False
    assert bad_payload["dispatches"][0]["land"]["on_master"] is False


def test_on_master_true_when_closeout_says_landed_false(tmp_path: Path) -> None:
    """Closeout landed false must not hide a head that master already contains."""
    ledger = CursorDispatchLedger.instance()
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    master_sha = _commit_file(repo, "landed.txt", "on master\n")
    _insert_dispatch(
        ledger,
        dispatch_id="disp-closeout-false-on-master",
        source_repo=repo,
    )
    _write_sidecar(
        repo,
        "disp-closeout-false-on-master",
        {"head_sha": master_sha, "landed": False, "status": "partial"},
    )
    payload = work_key_ledger_projection(_KEY, ledger=ledger)
    land = payload["dispatches"][0]["land"]
    assert land["closeout_landed"] is False
    assert land["closeout_head_sha"] == master_sha
    assert land["basis"] == "closeout"
    assert land["on_master"] is True
    assert land["landed_now"] is None
    assert land["commits_ahead"] is None
    assert _ledger_check_predicate(payload) is False


def test_landed_now_true_when_commits_ahead_one_on_master_head(
    tmp_path: Path,
) -> None:
    ledger = CursorDispatchLedger.instance()
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    master_sha = _commit_file(repo, "advance.txt", "landed commit\n")
    _insert_dispatch(
        ledger,
        dispatch_id="disp-commits-ahead-one",
        source_repo=repo,
    )
    _write_sidecar(
        repo,
        "disp-commits-ahead-one",
        {
            "head_sha": master_sha,
            "landed": True,
            "commits_ahead": 1,
            "status": "complete",
        },
    )
    payload = work_key_ledger_projection(_KEY, ledger=ledger)
    land = payload["dispatches"][0]["land"]
    assert land["on_master"] is True
    assert land["commits_ahead"] == 1
    assert land["landed_now"] is True
    assert _ledger_check_predicate(payload) is True


def test_landed_now_false_when_commits_ahead_zero_at_branch_point(
    tmp_path: Path,
) -> None:
    ledger = CursorDispatchLedger.instance()
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    branch_point_sha = _commit_file(repo, "initial.txt", "branch point\n")
    _insert_dispatch(
        ledger,
        dispatch_id="disp-commits-ahead-zero",
        source_repo=repo,
    )
    _write_sidecar(
        repo,
        "disp-commits-ahead-zero",
        {
            "head_sha": branch_point_sha,
            "landed": True,
            "commits_ahead": 0,
            "status": "complete",
        },
    )
    payload = work_key_ledger_projection(_KEY, ledger=ledger)
    land = payload["dispatches"][0]["land"]
    assert land["on_master"] is True
    assert land["commits_ahead"] == 0
    assert land["landed_now"] is False
    assert _ledger_check_predicate(payload) is False


def test_landed_now_null_when_commits_ahead_missing(tmp_path: Path) -> None:
    ledger = CursorDispatchLedger.instance()
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    master_sha = _commit_file(repo, "no-meter.txt", "on master\n")
    _insert_dispatch(
        ledger,
        dispatch_id="disp-no-commits-ahead",
        source_repo=repo,
    )
    _write_sidecar(
        repo,
        "disp-no-commits-ahead",
        {"head_sha": master_sha, "landed": True, "status": "complete"},
    )
    payload = work_key_ledger_projection(_KEY, ledger=ledger)
    land = payload["dispatches"][0]["land"]
    assert land["on_master"] is True
    assert land["commits_ahead"] is None
    assert land["landed_now"] is None
    assert _ledger_check_predicate(payload) is False


def test_fifty_dispatches_two_hundred_paths_stays_within_budget() -> None:
    ledger = CursorDispatchLedger.instance()
    repo = Path("/tmp/ledger-budget-repo")
    paths = [f"services/git_integration_worker/f{i}.py" for i in range(200)]
    now = datetime.now(UTC)
    for i in range(50):
        dispatch_id = f"disp-budget-{i:03d}"
        stamp = (now - timedelta(minutes=50 - i)).isoformat()
        structured = {
            "head_sha": f"{i:040x}",
            "landed": i == 49,
            "files_modified": paths,
            "files_modified_total": len(paths),
        }
        _insert_dispatch(
            ledger,
            dispatch_id=dispatch_id,
            source_repo=repo,
            queued_at=stamp,
            started_at=stamp,
            terminal_at=stamp,
        )
        _write_sidecar(repo, dispatch_id, structured)

    payload = work_key_ledger_projection(_KEY, limit=10, ledger=ledger)
    final_size = _compact_json_bytes(payload)
    assert final_size <= _BUDGET_BYTES
    assert payload["budget"]["actual_bytes"] == final_size
    assert payload["budget"]["truncated"] is True
    assert payload["budget"]["total_dispatches"] == 50
    assert payload["budget"]["returned_dispatches"] < 50


def test_budget_block_included_in_byte_cap() -> None:
    """Pre-budget payload may sit just under 5120; returned JSON must include budget."""
    shell: dict = {
        "work_key": _KEY,
        "limit": 5,
        "window_hours": 24,
        "total_dispatches": 1,
        "house_rollup": {
            "live_dispatches": [],
            "live_count": 0,
            "model_mix_24h": [],
            "usage_24h": {"rows_covered": 0, "rows_total": 0},
        },
    }
    pre_payload: dict | None = None
    pre_size = 0
    for pad in range(4800, 5120):
        trial = {
            **shell,
            "dispatches": [{"dispatch_id": "disp-pad", "filler": "z" * pad}],
        }
        size = _compact_json_bytes(trial)
        if size < _BUDGET_BYTES:
            pre_payload = trial
            pre_size = size
    assert pre_payload is not None
    assert pre_size >= _BUDGET_BYTES - 120

    result = _enforce_5kb_budget(pre_payload)
    final_size = _compact_json_bytes(result)
    assert final_size <= _BUDGET_BYTES
    assert result["budget"]["actual_bytes"] == final_size
    assert "budget" in result


def test_missing_sidecar_and_corrupt_record_json_do_not_raise() -> None:
    ledger = CursorDispatchLedger.instance()
    _insert_dispatch(
        ledger,
        dispatch_id="disp-no-sidecar",
        source_repo=None,
        record_json="{not-json",
    )
    _insert_dispatch(
        ledger,
        dispatch_id="disp-corrupt-rec",
        source_repo=Path("/tmp/missing-repo"),
        record_json="[]",
    )
    payload = work_key_ledger_projection(_KEY, ledger=ledger)
    assert payload["dispatches"]
    ids = {row["dispatch_id"] for row in payload["dispatches"]}
    assert "disp-no-sidecar" in ids
    assert "disp-corrupt-rec" in ids
    for row in payload["dispatches"]:
        assert row.get("usage_cost") is None
        assert row.get("usage_cost_absent_reason")


def test_work_key_ledger_route_empty_work_key_422() -> None:
    app = FastAPI()
    app.include_router(claim_router)
    client = TestClient(app)
    resp = client.get("/api/v1/cursor/work-key-ledger", params={"work_key": ""})
    assert resp.status_code == 422


def test_work_key_ledger_route_work_key_over_256_rejected() -> None:
    """Oversized work_key must fail Query validation before ledger serialization (C1)."""
    app = FastAPI()
    app.include_router(claim_router)
    client = TestClient(app)
    long_key = "todo:" + "x" * 252
    assert len(long_key.encode("utf-8")) == 257
    resp = client.get("/api/v1/cursor/work-key-ledger", params={"work_key": long_key})
    assert resp.status_code == 422


def test_work_key_ledger_route_work_key_over_256_utf8_bytes_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multibyte key under Query max_length must not reach projection or SQL (C1b)."""
    projection_calls: list[str] = []

    def _fake_projection(work_key: str, **kwargs: object) -> dict:
        projection_calls.append(work_key)
        return {}

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_dispatch_read.work_key_ledger_projection",
        _fake_projection,
    )
    app = FastAPI()
    app.include_router(claim_router)
    client = TestClient(app)
    long_key = "todo:" + "é" * 126
    assert len(long_key) < 256
    assert len(long_key.encode("utf-8")) == 257
    resp = client.get("/api/v1/cursor/work-key-ledger", params={"work_key": long_key})
    assert resp.status_code == 422
    assert projection_calls == []


def test_live_count_reflects_full_live_rows_when_budget_trims_list() -> None:
    """live_count stays pre-trim total while live_dispatches shrinks under budget (C2)."""
    live_rows = [
        {
            "dispatch_id": f"live-{i:03d}",
            "status": "running",
            "model": "cursor/grok-4.7",
        }
        for i in range(80)
    ]
    filler = "z" * 4000
    pre_payload: dict = {
        "work_key": _KEY,
        "limit": 5,
        "window_hours": 24,
        "dispatches": [{"dispatch_id": "disp-fill", "filler": filler}],
        "total_dispatches": 1,
        "house_rollup": {
            "live_dispatches": list(live_rows),
            "live_count": len(live_rows),
            "model_mix_24h": [],
            "usage_24h": {"rows_covered": 0, "rows_total": 0},
        },
    }
    result = _enforce_5kb_budget(pre_payload)
    returned_live = result["house_rollup"]["live_dispatches"]
    assert result["house_rollup"]["live_count"] == 80
    assert len(returned_live) < 80


def test_claim_guard_ledger_route_does_not_invoke_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claim admit path stays isolated from ledger reads (ACK #5)."""
    calls: list[dict] = []

    def _fake_claim(**kwargs: object) -> dict:
        calls.append(dict(kwargs))
        return {"claim_id": "claim-guard", "work_key": kwargs.get("work_key")}

    monkeypatch.setattr(
        "services.git_integration_worker.routes.cursor_claim.claim",
        _fake_claim,
    )
    app = FastAPI()
    app.include_router(claim_router)
    client = TestClient(app)
    post = client.post(
        "/api/v1/cursor/claim",
        json={
            "work_key": _KEY,
            "holder_agent": "cursor-sdk",
            "thread_id": "18485",
        },
    )
    assert post.status_code == 200
    get = client.get("/api/v1/cursor/work-key-ledger", params={"work_key": _KEY})
    assert get.status_code == 200
    assert len(calls) == 1
