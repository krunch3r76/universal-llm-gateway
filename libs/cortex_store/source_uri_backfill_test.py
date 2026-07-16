"""Tests for atomic source_uri backfill."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cortex_store.source_uri_backfill import (
    DEFAULT_EXPECTED_COUNT,
    SourceUriBackfillCountMismatchError,
    residual_stranded_count,
    run_source_uri_backfill,
    select_stranded_rows,
)


def _insert_stranded(
    migrated_conn: sqlite3.Connection,
    entity_id: str,
    nested_uri: str,
    *,
    extra_attrs: dict[str, object] | None = None,
) -> None:
    attrs = {"source_uri": nested_uri, **(extra_attrs or {})}
    migrated_conn.execute(
        "INSERT INTO entities (id, type, name, attributes, created_at) "
        "VALUES (?, 'todo', ?, ?, '2026-07-15T00:00:00Z')",
        (entity_id, entity_id, json.dumps(attrs)),
    )


def _seed_n_stranded(migrated_conn: sqlite3.Connection, n: int) -> list[str]:
    ids: list[str] = []
    for i in range(n):
        eid = f"todo:backfill-{i}"
        _insert_stranded(migrated_conn, eid, f"agent-bus:4917#{i}")
        ids.append(eid)
    migrated_conn.commit()
    return ids


def test_select_stranded_rows(migrated_conn: sqlite3.Connection) -> None:
    _insert_stranded(migrated_conn, "todo:one", "agent-bus:1#1")
    migrated_conn.commit()
    rows = select_stranded_rows(migrated_conn)
    assert len(rows) == 1
    assert rows[0]["id"] == "todo:one"


def test_backfill_zero_rows_is_successful_noop(
    migrated_conn: sqlite3.Connection,
) -> None:
    result = run_source_uri_backfill(migrated_conn, dry_run=False, expected_count=0)
    assert result.stranded_count == 0
    assert result.repaired_count == 0
    assert result.residual_count == 0
    assert result.applied is False


def test_backfill_wrong_count_rolls_back(migrated_conn: sqlite3.Connection) -> None:
    _seed_n_stranded(migrated_conn, 3)
    with pytest.raises(SourceUriBackfillCountMismatchError):
        run_source_uri_backfill(
            migrated_conn,
            dry_run=False,
            expected_count=DEFAULT_EXPECTED_COUNT,
        )
    assert residual_stranded_count(migrated_conn) == 3


def test_backfill_atomic_success_and_idempotent_rerun(
    migrated_conn: sqlite3.Connection,
) -> None:
    ids = _seed_n_stranded(migrated_conn, DEFAULT_EXPECTED_COUNT)
    result = run_source_uri_backfill(
        migrated_conn,
        dry_run=False,
        expected_count=DEFAULT_EXPECTED_COUNT,
    )
    assert result.stranded_count == DEFAULT_EXPECTED_COUNT
    assert result.repaired_count == DEFAULT_EXPECTED_COUNT
    assert result.residual_count == 0
    assert result.applied is True
    assert residual_stranded_count(migrated_conn) == 0

    for i, eid in enumerate(ids):
        row = migrated_conn.execute(
            "SELECT source_uri, attributes FROM entities WHERE id = ?",
            (eid,),
        ).fetchone()
        assert row["source_uri"] == f"agent-bus:4917#{i}"
        attrs = json.loads(row["attributes"])
        assert "source_uri" not in attrs

    rerun = run_source_uri_backfill(
        migrated_conn,
        dry_run=False,
        expected_count=DEFAULT_EXPECTED_COUNT,
    )
    assert rerun.stranded_count == 0
    assert rerun.applied is False


def test_backfill_dry_run_rolls_back(migrated_conn: sqlite3.Connection) -> None:
    _seed_n_stranded(migrated_conn, 2)
    result = run_source_uri_backfill(migrated_conn, dry_run=True, expected_count=2)
    assert result.dry_run is True
    assert result.applied is False
    assert residual_stranded_count(migrated_conn) == 2


def test_backfill_preserves_unsupplied_legacy_attributes(
    migrated_conn: sqlite3.Connection,
) -> None:
    _insert_stranded(
        migrated_conn,
        "todo:legacy-alias",
        "agent-bus:42",
        extra_attrs={"files_modified": ["legacy.py"], "domain": "cortex"},
    )
    migrated_conn.commit()

    result = run_source_uri_backfill(migrated_conn, dry_run=False, expected_count=1)

    assert result.repaired_count == 1
    row = migrated_conn.execute(
        "SELECT source_uri, attributes FROM entities WHERE id = 'todo:legacy-alias'"
    ).fetchone()
    attrs = json.loads(row["attributes"])
    assert row["source_uri"] == "agent-bus:42"
    assert attrs == {"files_modified": ["legacy.py"], "domain": "cortex"}


def test_backfill_conflict_rolls_back(
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from cortex_store import source_uri_backfill as backfill_mod

    _insert_stranded(migrated_conn, "todo:conflict-a", "agent-bus:9#9")
    _insert_stranded(migrated_conn, "todo:conflict-b", "agent-bus:8#8")
    migrated_conn.commit()

    original = backfill_mod.update_entity_impl
    calls = {"n": 0}

    def _fail_second(*args: object, **kwargs: object) -> dict[str, object]:
        calls["n"] += 1
        if calls["n"] == 2:
            raise HTTPException(status_code=422, detail="simulated conflict")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(backfill_mod, "update_entity_impl", _fail_second)

    with pytest.raises(HTTPException):
        run_source_uri_backfill(migrated_conn, dry_run=False, expected_count=2)
    assert residual_stranded_count(migrated_conn) == 2


def test_backfill_uses_shared_update_path(migrated_conn: sqlite3.Connection) -> None:
    _insert_stranded(
        migrated_conn, "todo:shared", "agent-bus:42#42", extra_attrs={"domain": "x"}
    )
    migrated_conn.commit()
    result = run_source_uri_backfill(migrated_conn, dry_run=False, expected_count=1)
    assert result.repaired_count == 1
    row = migrated_conn.execute(
        "SELECT source_uri, attributes FROM entities WHERE id = 'todo:shared'"
    ).fetchone()
    attrs = json.loads(row["attributes"])
    assert row["source_uri"] == "agent-bus:42#42"
    assert attrs == {"domain": "x"}
