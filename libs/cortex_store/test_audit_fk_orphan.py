"""Hermetic tests for foreign_key_orphan audit detector."""

from __future__ import annotations

import sqlite3

import pytest

from cortex_store.dispatch_ops._detectors.fk_orphan import detect_foreign_key_orphan
from cortex_store.dispatch_ops.ops_audit import _op_audit


@pytest.mark.offline
def test_detect_foreign_key_orphan_reports_missing_parent(
    migrated_conn: sqlite3.Connection,
) -> None:
    orphan_id = "todo:fk-orphan-audit-test"
    migrated_conn.execute("PRAGMA foreign_keys=OFF")
    migrated_conn.execute(
        "INSERT INTO entity_salience_cache (entity_id, salience_score) VALUES (?, 0.5)",
        (orphan_id,),
    )
    migrated_conn.execute("PRAGMA foreign_keys=ON")
    migrated_conn.commit()

    findings = detect_foreign_key_orphan(migrated_conn)
    kinds = {f["kind"] for f in findings}
    subjects = {f["subject"] for f in findings}
    assert "foreign_key_orphan" in kinds
    assert orphan_id in subjects


@pytest.mark.offline
def test_audit_op_surfaces_foreign_key_orphan(
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orphan_id = "todo:fk-orphan-audit-op"
    migrated_conn.execute("PRAGMA foreign_keys=OFF")
    migrated_conn.execute(
        "INSERT INTO entity_salience_cache (entity_id, salience_score) VALUES (?, 0.5)",
        (orphan_id,),
    )
    migrated_conn.execute("PRAGMA foreign_keys=ON")
    migrated_conn.commit()

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_audit_detectors.cortex_conn",
        lambda: migrated_conn,
    )

    result = _op_audit(kinds=["foreign_key_orphan"], emit=False)
    assert result["gap_count"] >= 1
    assert any(f["kind"] == "foreign_key_orphan" for f in result["findings"])


@pytest.mark.offline
def test_detect_foreign_key_orphan_clean_when_none(
    migrated_conn: sqlite3.Connection,
) -> None:
    assert detect_foreign_key_orphan(migrated_conn) == []
