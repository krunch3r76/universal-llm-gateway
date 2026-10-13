"""S1 birth atomicity — age writers must not mint root_ledger authority rows."""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner import (
    enrollment_filter,
    ledger_age,
)
from scripts.model_manager.ui.controller.charter_runner.admission.typed_work_item import (
    TypedWorkItemAdmit,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
    admit_work_item,
    load_root,
    upsert_root,
)
from scripts.model_manager.ui.controller.charter_runner.seed_phase1 import (
    ensure_root_ledger_seed,
)


@pytest.fixture
def ledger_dir(tmp_path: Path) -> Path:
    data = tmp_path / "ledger"
    data.mkdir()
    return data


def _ledger_conn(ledger_dir: Path):
    return open_ledger_db(ledger_dir / "root-ledger.sqlite")


def _root_ledger_count(conn, root_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM root_ledger WHERE root_id = ?",
        (root_id,),
    ).fetchone()
    return int(row[0])


def _age_clock_count(conn, cls: str, key: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) FROM age_clock
        WHERE clock_class = ? AND clock_key = ?
        """,
        (cls, key),
    ).fetchone()
    return int(row[0])


def _stub_row(root_id: str) -> RootLedgerRow:
    return RootLedgerRow(
        root_id=root_id,
        status=RootStatus.IDLE,
        pickup_gid=None,
        pickup_lane=None,
        pickup_executor=None,
        attendance="autonomous",
        scoreboard_uri="",
    )


@pytest.mark.offline
def test_observe_tick_stall_does_not_insert_root_ledger(ledger_dir: Path) -> None:
    root_id = "9999"
    ledger_age.observe("tick_stall", root_id, present=True, data_dir=ledger_dir)

    conn = _ledger_conn(ledger_dir)
    try:
        assert _root_ledger_count(conn, root_id) == 0
        assert _age_clock_count(conn, "tick_stall", root_id) == 1
    finally:
        conn.close()


@pytest.mark.offline
def test_observe_tick_stall_refuse_does_not_insert_root_ledger(ledger_dir: Path) -> None:
    key = "9999:refuse"
    ledger_age.observe("tick_stall", key, present=True, data_dir=ledger_dir)

    conn = _ledger_conn(ledger_dir)
    try:
        assert _root_ledger_count(conn, "9999") == 0
        assert _age_clock_count(conn, "tick_stall", key) == 1
    finally:
        conn.close()


@pytest.mark.offline
def test_migrated_cache_ignores_invalid_stub_row(
    ledger_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        upsert_root(conn, _stub_row("stub-root"))
    finally:
        conn.close()

    monkeypatch.setattr(
        enrollment_filter,
        "open_default_ledger",
        lambda: _ledger_conn(ledger_dir),
    )
    enrollment_filter._migrated_cache = frozenset()
    migrated = enrollment_filter.refresh_migrated_roots_cache()
    assert "stub-root" not in migrated


@pytest.mark.offline
def test_migrated_cache_includes_valid_admit_row(
    ledger_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        admit_work_item(
            conn,
            TypedWorkItemAdmit(
                root_id="7777",
                pickup_gid="G1",
                pickup_lane="judgment",
                attendance="autonomous",
                scoreboard_uri="cortex://notes/system/threads/7777-charter-scoreboard.md",
            ),
        )
    finally:
        conn.close()

    monkeypatch.setattr(
        enrollment_filter,
        "open_default_ledger",
        lambda: _ledger_conn(ledger_dir),
    )
    enrollment_filter._migrated_cache = frozenset()
    migrated = enrollment_filter.refresh_migrated_roots_cache()
    assert "7777" in migrated


@pytest.mark.offline
def test_ensure_root_ledger_seed_false_for_stub_only(
    ledger_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        upsert_root(conn, _stub_row("8888"))
    finally:
        conn.close()

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.seed_phase1.open_default_ledger",
        lambda: _ledger_conn(ledger_dir),
    )
    assert ensure_root_ledger_seed("8888") is False


@pytest.mark.offline
def test_ensure_root_ledger_seed_true_for_valid_admit_without_re_admit(
    ledger_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        admit_work_item(
            conn,
            TypedWorkItemAdmit(
                root_id="7777",
                pickup_gid="G2",
                pickup_lane="mechanical",
                attendance="attended",
                scoreboard_uri="cortex://notes/system/threads/7777-charter-scoreboard.md",
            ),
        )
    finally:
        conn.close()

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.seed_phase1.open_default_ledger",
        lambda: _ledger_conn(ledger_dir),
    )
    assert ensure_root_ledger_seed("7777") is True
    conn = _ledger_conn(ledger_dir)
    try:
        row = load_root(conn, "7777")
        assert row is not None
        assert row.pickup_gid == "G2"
        assert row.status == RootStatus.IDLE
    finally:
        conn.close()
