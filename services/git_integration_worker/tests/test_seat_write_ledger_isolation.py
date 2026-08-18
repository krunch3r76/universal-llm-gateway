"""Seat-write ledger pytest refuse belt and live-db isolation."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest
from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    capture_wt_baseline_with_hashes,
    prepare_closeout_delivery,
)
from services.git_integration_worker.seat_write_ledger import (
    SeatWriteLedger,
    _ledger_path,
)

pytestmark = pytest.mark.offline

_LIVE_DB = Path.home() / ".gateway" / "seat-write-ledger.db"
_PROBE_ARC = "d-isolation-probe"


def _count_live_d_arcs() -> int:
    if not _LIVE_DB.exists():
        return 0
    conn = sqlite3.connect(f"file:{_LIVE_DB}?mode=ro", uri=True)
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM seat_write_arcs WHERE arc_id LIKE 'd-%'"
            ).fetchone()[0]
        )
    finally:
        conn.close()


def _init_git_repo_with_commit(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "test"],
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "seed"],
        check=True,
        capture_output=True,
    )


def test_seat_write_ledger_path_refuses_the_live_gateway_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail the suite if ``_ledger_path`` would open ``~/.gateway`` under pytest."""
    monkeypatch.setenv("DATA_DIR", str(Path.home() / ".gateway"))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_seat_write_ledger_isolation")
    SeatWriteLedger.reset_instance()
    with pytest.raises(
        RuntimeError, match="refusing to open the live seat-write ledger"
    ):
        _ledger_path()


def test_prepare_closeout_delivery_does_not_insert_into_live_seat_write_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unpatched ``d-%`` closeout must not add a production ``seat_write_arcs`` row."""
    from services.git_integration_worker.cursor_sdk_closeout.delivery_assembly import (
        change_set_resolution,
    )

    _init_git_repo_with_commit(tmp_path)
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    rel = "services/written.py"
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text("x=1\n", encoding="utf-8")

    cfg = WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=tmp_path,
        worktree_root=tmp_path / "wt",
        dispatch_workspace=tmp_path / "ws",
        green_gate_cmd=["true"],
    )
    monkeypatch.setattr(change_set_resolution, "load_config", lambda: cfg)

    SeatWriteLedger.reset_instance()
    before = _count_live_d_arcs()
    manifest = EffectsManifest(
        dispatch_id=_PROBE_ARC,
        thread_id="t-iso",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[EffectEntry(op="write", target=rel, identity=rel)],
            )
        },
        coverage={"repo": "complete"},
    )
    prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id=_PROBE_ARC,
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=50,
            tool_call_count=2,
            effects_manifest=manifest,
        ),
        degraded_reason=None,
        thread_id="t-iso",
        work_item_ref=None,
        baseline=baseline,
        packet_text=f"<scope>\nFiles expected:\n- `{rel}`\n</scope>\n",
        cortex_artifact_paths=[],
        gate_d_created_rels=(),
        deliverables_expected=True,
    )
    after = _count_live_d_arcs()
    assert after == before
    assert SeatWriteLedger.instance().has_paths_for_arc(arc_id=_PROBE_ARC) is True
    live_path = (Path.home() / ".gateway").resolve()
    bound = SeatWriteLedger.instance()._db_path.resolve()
    assert bound != live_path / "seat-write-ledger.db"
    assert not bound.is_relative_to(live_path)
