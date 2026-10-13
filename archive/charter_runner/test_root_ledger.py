"""RootLedger migration + seed tests (P1-AC4)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from libs.charter_runner_store.db import apply_migrations, open_ledger_db
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootStatus,
    RootLedgerRow,
    SeedConfirm,
    load_all_roots,
    seed_from_confirm,
    write_cortex_mirror,
)


@pytest.mark.offline
def test_migration_001_applies_on_memory() -> None:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "root_ledger" in tables
    assert "consult_queue" in tables
    assert "ledger_meta" in tables


@pytest.mark.offline
def test_seed_confirm_writes_idle_row(tmp_path: Path) -> None:
    db = tmp_path / "ledger.sqlite"
    conn = open_ledger_db(db)
    try:
        row = seed_from_confirm(
            conn,
            SeedConfirm(
                root_id="5975",
                pickup_gid="G7",
                pickup_lane="judgment",
                attendance="autonomous",
                scoreboard_uri="cortex://notes/system/threads/5975-charter-scoreboard.md",
            ),
        )
        assert row.status == RootStatus.IDLE
        assert row.pickup_gid == "G7"
        loaded = load_all_roots(conn)
        assert len(loaded) == 1
        assert loaded[0].attendance == "autonomous"
    finally:
        conn.close()


@pytest.mark.offline
def test_ensure_root_ledger_seed_6171_attended_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import seed_phase1

    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.default_ledger_path",
        lambda: db,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.seed_phase1.open_default_ledger",
        lambda: open_ledger_db(db),
    )
    assert any(s.root_id == "6171" and s.attendance == "attended" for s in seed_phase1.PHASE1_SEEDS)
    assert seed_phase1.ensure_root_ledger_seed("6171") is True
    conn = open_ledger_db(db)
    try:
        row = load_all_roots(conn)[0]
        assert row.root_id == "6171"
        assert row.status == RootStatus.IDLE
        assert row.attendance == "attended"
        assert row.pickup_gid == "G9"
    finally:
        conn.close()
    # Idempotent — must not clobber after a simulated pickup change.
    conn = open_ledger_db(db)
    try:
        from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
            RootLedgerRow,
            upsert_root,
        )

        existing = load_all_roots(conn)[0]
        upsert_root(
            conn,
            RootLedgerRow(
                root_id=existing.root_id,
                status=existing.status,
                pickup_gid="G12",
                pickup_lane=existing.pickup_lane,
                pickup_executor=existing.pickup_executor,
                attendance=existing.attendance,
                scoreboard_uri=existing.scoreboard_uri,
            ),
        )
    finally:
        conn.close()
    assert seed_phase1.ensure_root_ledger_seed("6171") is True
    conn = open_ledger_db(db)
    try:
        assert load_all_roots(conn)[0].pickup_gid == "G12"
    finally:
        conn.close()


@pytest.mark.offline
def test_ensure_root_ledger_seed_unknown_without_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import seed_phase1

    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.seed_phase1.open_default_ledger",
        lambda: open_ledger_db(db),
    )
    assert seed_phase1.ensure_root_ledger_seed("9999") is False
    conn = open_ledger_db(db)
    try:
        assert load_all_roots(conn) == []
    finally:
        conn.close()


def _sample_row(root_id: str = "5975") -> RootLedgerRow:
    return RootLedgerRow(
        root_id=root_id,
        status=RootStatus.IDLE,
        pickup_gid="G1",
        pickup_lane="B",
        pickup_executor="cursor-sdk",
        attendance="autonomous",
        scoreboard_uri="cortex://notes/system/threads/5975-charter-scoreboard.md",
    )


@pytest.mark.offline
def test_write_cortex_mirror_shared_root_returns_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entity write path fails closed; shared CORTEX_FILES_ROOT still emits cortex://."""
    cortex_root = tmp_path / "cortex-files"
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    uri = write_cortex_mirror(_sample_row())
    assert uri == "cortex://notes/system/threads/charter-ledger/5975.json"
    mirror = cortex_root / "notes/system/threads/charter-ledger/5975.json"
    assert mirror.is_file()


@pytest.mark.offline
def test_write_cortex_mirror_home_only_quarantines_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HOME-only fallback must not mint a cortex:// delivery pointer."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger._write_mirror_to_shared_root",
        lambda _uri, _content: False,
    )
    uri = write_cortex_mirror(_sample_row())
    assert uri == ""
    home_mirror = (
        home / ".local/share/cortex/notes/system/threads/charter-ledger/5975.json"
    )
    assert home_mirror.is_file()
