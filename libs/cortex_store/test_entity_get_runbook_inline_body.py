"""entity_get runbook body inline + argument surface (lane 13288)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store._intent_card_test_fixtures import insert_assertion, insert_entity
from cortex_store.dispatch_ops import execute_op
from cortex_store.runbook_inline_body import RUNBOOK_INLINE_BODY_MAX_BYTES


@contextmanager
def _patched_conn(conn: sqlite3.Connection):
    class _Ctx:
        def __enter__(self) -> sqlite3.Connection:
            return conn

        def __exit__(self, *a: object) -> None:
            return None

    with patch("cortex_store.dispatch_ops.ops_entities.cortex_conn", _Ctx):
        yield


def _insert_runbook(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    source_uri: str,
    description: str,
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO entities (id, type, name, description, source_uri, "
        "confidence_band, created_at, updated_at) "
        "VALUES (?, 'runbook', ?, ?, ?, 'confirmed', ?, ?)",
        (entity_id, entity_id.removeprefix("runbook:"), description, source_uri, now, now),
    )
    conn.commit()


@pytest.mark.offline
def test_runbook_card_inlines_body_by_default(
    migrated_conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    body_text = "# Maestro\n\n## trigger\nWake.\n"
    md = tmp_path / "maestro-loop.md"
    md.write_text(body_text, encoding="utf-8")
    desc = (
        "Invoked command for the Cowork life-seat operator loop. "
        "Body: cortex://notes/runbooks/maestro-loop.md. "
        "Sections: trigger, refuse, steps, falsifier."
    )
    _insert_runbook(
        migrated_conn,
        entity_id="runbook:maestro-loop",
        source_uri="notes/runbooks/maestro-loop.md",
        description=desc,
    )
    insert_assertion(
        migrated_conn,
        entity_id="runbook:maestro-loop",
        claim="runbook registered",
        confidence="confirmed",
    )

    def _resolve(source_uri: str, slug: str) -> Path | None:
        return md

    with (
        _patched_conn(migrated_conn),
        patch(
            "cortex_store.routes.boot._skill_trigger._resolve_skill_file",
            side_effect=_resolve,
        ),
    ):
        before_shape = execute_op(
            "entity_get",
            {"entity_id": "runbook:maestro-loop", "intent": "card", "include_body": True},
        )

    assert "error" not in before_shape
    assert before_shape["body"] == body_text
    assert before_shape["body_inline"]["truncated"] is False
    assert before_shape["summary_row"] == desc
    assert before_shape["status_summary"]["status"] == "confirmed"
    assert before_shape["assertion_counts"]["active"] == 1
    assert [s["id"] for s in before_shape["section_manifest"]] == [
        "assertions",
        "assertions_superseded",
        "relationships",
        "archives_to",
        "reasoning_edges",
    ]
    assert "predicate_summary" in before_shape
    assert "freshness" in before_shape
    assert "top_k_assertions" in before_shape


@pytest.mark.offline
def test_include_body_false_omits_runbook_body(
    migrated_conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    md = tmp_path / "x.md"
    md.write_text("# x\n", encoding="utf-8")
    _insert_runbook(
        migrated_conn,
        entity_id="runbook:omit-body",
        source_uri="notes/runbooks/x.md",
        description="Body: cortex://notes/runbooks/x.md.",
    )
    with (
        _patched_conn(migrated_conn),
        patch(
            "cortex_store.routes.boot._skill_trigger._resolve_skill_file",
            lambda _uri, _slug: md,
        ),
    ):
        result = execute_op(
            "entity_get",
            {
                "entity_id": "runbook:omit-body",
                "intent": "card",
                "include_body": False,
            },
        )
    assert "body" not in result


@pytest.mark.offline
def test_runbook_body_truncation_surfaces_metadata(
    migrated_conn: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cortex_store.runbook_inline_body.RUNBOOK_INLINE_BODY_MAX_BYTES",
        32,
    )
    huge = "x" * 200
    md = tmp_path / "big.md"
    md.write_text(huge, encoding="utf-8")
    _insert_runbook(
        migrated_conn,
        entity_id="runbook:big",
        source_uri="notes/runbooks/big.md",
        description="big runbook",
    )
    with (
        _patched_conn(migrated_conn),
        patch(
            "cortex_store.routes.boot._skill_trigger._resolve_skill_file",
            lambda _uri, _slug: md,
        ),
    ):
        result = execute_op(
            "entity_get",
            {"entity_id": "runbook:big", "intent": "card"},
        )
    assert result["body_inline"]["truncated"] is True
    assert result["body_inline"]["byte_count"] == len(huge.encode("utf-8"))
    assert result["body_inline"]["read_full_at"] == "cortex://notes/runbooks/big.md"
    assert len(result["body"].encode("utf-8")) <= 32


@pytest.mark.offline
def test_unknown_entity_get_args_surface_validation_warnings(
    migrated_conn: sqlite3.Connection,
) -> None:
    insert_entity(
        migrated_conn,
        entity_id="todo:wire",
        entity_type="todo",
        name="wire",
    )
    with _patched_conn(migrated_conn):
        result = execute_op(
            "entity_get",
            {
                "entity_id": "todo:wire",
                "intent": "card",
                "include_body": True,
                "bogus_flag": 1,
            },
        )
    warnings = result.get("validation_warnings") or []
    fields = {w["field"] for w in warnings}
    assert "bogus_flag" in fields
    assert "include_body" in fields


@pytest.mark.offline
def test_cap_default_matches_mcp_headroom_document() -> None:
    assert RUNBOOK_INLINE_BODY_MAX_BYTES == 20 * 1024


@pytest.mark.offline
def test_before_after_include_body_no_longer_silent(
    migrated_conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    """Document AC3: include_body=true must change payload vs omitted on runbook."""
    md = tmp_path / "m.md"
    md.write_text("# m\n", encoding="utf-8")
    _insert_runbook(
        migrated_conn,
        entity_id="runbook:m",
        source_uri="notes/runbooks/m.md",
        description="d",
    )
    with (
        _patched_conn(migrated_conn),
        patch(
            "cortex_store.routes.boot._skill_trigger._resolve_skill_file",
            lambda _u, _s: md,
        ),
    ):
        without = execute_op(
            "entity_get",
            {"entity_id": "runbook:m", "intent": "card", "include_body": False},
        )
        with_flag = execute_op(
            "entity_get",
            {"entity_id": "runbook:m", "intent": "card", "include_body": True},
        )
    assert "body" not in without
    assert "body" in with_flag
    # Serialized shapes differ — prior silent-drop made these identical.
    assert json.dumps(without, sort_keys=True) != json.dumps(with_flag, sort_keys=True)
