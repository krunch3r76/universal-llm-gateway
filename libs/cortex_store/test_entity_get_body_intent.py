"""entity_get intent=body — default returns full markdown, not size-aware manifest."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops import execute_op


@contextmanager
def _patched_conn(conn: sqlite3.Connection):
    class _Ctx:
        def __enter__(self) -> sqlite3.Connection:
            return conn

        def __exit__(self, *a: object) -> None:
            return None

    with patch("cortex_store.dispatch_ops.ops_entities.cortex_conn", _Ctx):
        yield


def _insert_entity_with_source(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    source_uri: str,
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO entities (id, type, name, description, source_uri, created_at, updated_at) "
        "VALUES (?, 'workflow', ?, 'Body intent probe.', ?, ?, ?)",
        (entity_id, entity_id.removeprefix("workflow:"), source_uri, now, now),
    )
    conn.commit()


@pytest.mark.offline
def test_entity_get_body_default_returns_full_for_large_doc(
    migrated_conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    """Friction 21675: docs >5000 chars must not silently downgrade to manifest."""
    body_file = tmp_path / "large-workflow.md"
    large_text = "# Large workflow\n\n" + ("paragraph text. " * 400)
    assert len(large_text) > 5000
    body_file.write_text(large_text, encoding="utf-8")

    entity_id = "workflow:body-intent-large"
    _insert_entity_with_source(
        migrated_conn,
        entity_id=entity_id,
        source_uri="notes/system/workflows/large-workflow.md",
    )

    with (
        _patched_conn(migrated_conn),
        patch(
            "cortex_store.routes.boot._skill_trigger._resolve_skill_file",
            return_value=body_file,
        ),
    ):
        result = execute_op(
            "entity_get",
            {"entity_id": entity_id, "intent": "body"},
        )

    assert "error" not in result
    assert result["render_mode"] == "full"
    assert result["body"] == large_text


@pytest.mark.offline
def test_entity_get_body_full_body_false_returns_manifest(
    migrated_conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    body_file = tmp_path / "manifest-workflow.md"
    text = "# Title\n\n## Section A\n\nContent A.\n\n## Section B\n\nContent B."
    body_file.write_text(text, encoding="utf-8")

    entity_id = "workflow:body-intent-manifest"
    _insert_entity_with_source(
        migrated_conn,
        entity_id=entity_id,
        source_uri="notes/system/workflows/manifest-workflow.md",
    )

    with (
        _patched_conn(migrated_conn),
        patch(
            "cortex_store.routes.boot._skill_trigger._resolve_skill_file",
            return_value=body_file,
        ),
    ):
        result = execute_op(
            "entity_get",
            {
                "entity_id": entity_id,
                "intent": "body",
                "full_body": False,
            },
        )

    assert "error" not in result
    assert result["render_mode"] == "manifest"
    assert "sections" in result
    assert "body" not in result
