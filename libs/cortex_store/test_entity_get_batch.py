"""entity_get batch — multiple entities, shared parameters."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store._intent_card_test_fixtures import insert_assertion, insert_entity
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
        "VALUES (?, 'workflow', ?, 'Batch body probe.', ?, ?, ?)",
        (entity_id, entity_id.removeprefix("workflow:"), source_uri, now, now),
    )
    conn.commit()


def _access_log_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM entity_access_log").fetchone()
    return int(row["n"])


@pytest.mark.offline
def test_batch_body_returns_items_with_render_mode(
    migrated_conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    bodies: dict[str, str] = {}
    entity_ids: list[str] = []
    for slug in ("alpha", "beta"):
        body_file = tmp_path / f"{slug}.md"
        text = f"# {slug}\n\nBody for {slug}."
        body_file.write_text(text, encoding="utf-8")
        entity_id = f"workflow:batch-body-{slug}"
        entity_ids.append(entity_id)
        bodies[entity_id] = text
        _insert_entity_with_source(
            migrated_conn,
            entity_id=entity_id,
            source_uri=f"notes/system/workflows/{slug}.md",
        )

    def _resolve(source_uri: str, slug: str) -> Path | None:
        for eid in entity_ids:
            if slug in eid:
                return tmp_path / f"{slug.split('-')[-1]}.md"
        return None

    with (
        _patched_conn(migrated_conn),
        patch(
            "cortex_store.routes.boot._skill_trigger._resolve_skill_file",
            side_effect=_resolve,
        ),
    ):
        result = execute_op(
            "entity_get",
            {"entity_ids": entity_ids, "intent": "body"},
        )

    assert "error" not in result
    assert result["count"] == 2
    assert len(result["items"]) == 2
    for item, eid in zip(result["items"], entity_ids, strict=True):
        assert item["ok"] is True
        assert item["input_entity_id"] == eid
        assert item["resolved_entity_id"] == eid
        assert item["render_mode"] == "full"
        assert item["body"] == bodies[eid]


@pytest.mark.offline
def test_batch_card_returns_card_payloads(
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_ids = ["todo:batch-card-a", "todo:batch-card-b"]
    for eid in entity_ids:
        insert_entity(migrated_conn, entity_id=eid, entity_type="todo")
        insert_assertion(migrated_conn, entity_id=eid, claim=f"Claim for {eid}.")

    with _patched_conn(migrated_conn):
        result = execute_op(
            "entity_get",
            {"entity_ids": entity_ids, "intent": "card"},
        )

    assert "error" not in result
    assert result["count"] == 2
    for item, eid in zip(result["items"], entity_ids, strict=True):
        assert item["ok"] is True
        assert item["input_entity_id"] == eid
        assert item["resolved_entity_id"] == eid
        assert item["id"] == eid
        assert "top_k_assertions" in item


@pytest.mark.offline
def test_batch_partial_failure_unknown_id(
    migrated_conn: sqlite3.Connection,
) -> None:
    known = "todo:batch-partial-known"
    insert_entity(migrated_conn, entity_id=known, entity_type="todo")

    with _patched_conn(migrated_conn):
        result = execute_op(
            "entity_get",
            {
                "entity_ids": [known, "todo:batch-partial-missing"],
                "intent": "card",
            },
        )

    assert result["count"] == 2
    assert result["items"][0]["ok"] is True
    assert result["items"][0]["input_entity_id"] == known
    assert result["items"][1]["ok"] is False
    assert result["items"][1]["input_entity_id"] == "todo:batch-partial-missing"
    assert result["items"][1]["status_code"] == 404


@pytest.mark.offline
def test_batch_preserves_duplicate_input_ids(
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "todo:batch-dup"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="todo")

    with _patched_conn(migrated_conn):
        result = execute_op(
            "entity_get",
            {"entity_ids": [entity_id, entity_id], "intent": "card"},
        )

    assert result["count"] == 2
    assert result["items"][0]["input_entity_id"] == entity_id
    assert result["items"][1]["input_entity_id"] == entity_id
    assert result["items"][0]["ok"] is True
    assert result["items"][1]["ok"] is True


@pytest.mark.offline
def test_single_entity_responses_unchanged(
    migrated_conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    body_file = tmp_path / "single.md"
    body_text = "# Single\n\nUnchanged body."
    body_file.write_text(body_text, encoding="utf-8")
    entity_id = "workflow:batch-single-compat"
    _insert_entity_with_source(
        migrated_conn,
        entity_id=entity_id,
        source_uri="notes/system/workflows/single.md",
    )
    insert_entity(migrated_conn, entity_id="todo:batch-single-card", entity_type="todo")
    insert_assertion(
        migrated_conn,
        entity_id="todo:batch-single-card",
        claim="Single card compat.",
    )

    with (
        _patched_conn(migrated_conn),
        patch(
            "cortex_store.routes.boot._skill_trigger._resolve_skill_file",
            return_value=body_file,
        ),
    ):
        body = execute_op(
            "entity_get",
            {"entity_id": entity_id, "intent": "body"},
        )
        card = execute_op(
            "entity_get",
            {"entity_id": "todo:batch-single-card", "intent": "card"},
        )
        full = execute_op(
            "entity_get",
            {"entity_id": "todo:batch-single-card", "intent": "full"},
        )

    assert body["entity_id"] == entity_id
    assert body["source_uri"] == "notes/system/workflows/single.md"
    assert body["render_mode"] == "full"
    assert body["body"] == body_text
    assert card["id"] == "todo:batch-single-card"
    assert "assertions" in full


@pytest.mark.offline
def test_batch_validation_errors() -> None:
    over_cap = [f"todo:cap-{i}" for i in range(51)]
    cases = [
        ({"entity_ids": over_cap, "intent": "body"}, "batch_over_cap"),
        ({"entity_ids": [], "intent": "body"}, "entity_ids must not be empty"),
        ({"entity_ids": "todo:not-a-list", "intent": "body"}, "entity_ids must be a list"),
        ({"entity_ids": ["todo:a"], "intent": "full"}, "batch_intent_unsupported"),
        ({"entity_ids": ["todo:a"], "intent": "card-md"}, "batch_intent_unsupported"),
        (
            {"entity_ids": ["todo:a"], "intent": "full-historical"},
            "batch_intent_unsupported",
        ),
        (
            {
                "entity_id": "todo:a",
                "entity_ids": ["todo:b"],
                "intent": "body",
            },
            "both_entity_id_and_entity_ids",
        ),
    ]
    for args, err_substr in cases:
        result = execute_op("entity_get", args)
        assert "error" in result
        assert err_substr in str(result["error"])
        assert result.get("status_code") == 400


@pytest.mark.offline
def test_batch_over_cap_includes_received_and_max() -> None:
    over_cap = [f"todo:cap-meta-{i}" for i in range(51)]
    result = execute_op("entity_get", {"entity_ids": over_cap, "intent": "body"})
    assert result["error"] == "batch_over_cap"
    assert result["received"] == 51
    assert result["max"] == 50
    assert result["status_code"] == 400


@pytest.mark.offline
def test_batch_body_writes_no_access_log(
    migrated_conn: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    body_file = tmp_path / "log-body.md"
    body_file.write_text("# Log\n\nNo access log for body batch.", encoding="utf-8")
    entity_id = "workflow:batch-body-log"
    _insert_entity_with_source(
        migrated_conn,
        entity_id=entity_id,
        source_uri="notes/system/workflows/log-body.md",
    )
    before = _access_log_count(migrated_conn)

    with (
        _patched_conn(migrated_conn),
        patch(
            "cortex_store.routes.boot._skill_trigger._resolve_skill_file",
            return_value=body_file,
        ),
    ):
        result = execute_op(
            "entity_get",
            {"entity_ids": [entity_id, entity_id], "intent": "body"},
        )

    assert result["count"] == 2
    assert _access_log_count(migrated_conn) == before


@pytest.mark.offline
def test_batch_card_writes_access_log_per_success_including_duplicates(
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "todo:batch-card-log"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="todo")
    before = _access_log_count(migrated_conn)

    with _patched_conn(migrated_conn):
        result = execute_op(
            "entity_get",
            {"entity_ids": [entity_id, entity_id], "intent": "card"},
        )

    assert result["count"] == 2
    assert _access_log_count(migrated_conn) == before + 2


@pytest.mark.offline
def test_single_nonexistent_card_byte_identical_error_shape(
    migrated_conn: sqlite3.Connection,
) -> None:
    before = _access_log_count(migrated_conn)
    with _patched_conn(migrated_conn):
        result = execute_op(
            "entity_get",
            {"entity_id": "todo:batch-nonexistent-single", "intent": "card"},
        )
    assert result["error"] == "Entity entity not found: todo:batch-nonexistent-single"
    assert result["status_code"] == 404
    assert "_hint" in result
    assert _access_log_count(migrated_conn) == before


@pytest.mark.offline
def test_batch_nonexistent_returns_ok_false_item_not_500(
    migrated_conn: sqlite3.Connection,
) -> None:
    with _patched_conn(migrated_conn):
        result = execute_op(
            "entity_get",
            {"entity_ids": ["todo:batch-nonexistent-batch"], "intent": "card"},
        )
    assert "error" not in result
    assert result["count"] == 1
    item = result["items"][0]
    assert item["ok"] is False
    assert item["input_entity_id"] == "todo:batch-nonexistent-batch"
    assert item["status_code"] == 404


@pytest.mark.offline
def test_batch_intent_unsupported_lists_supported() -> None:
    result = execute_op(
        "entity_get",
        {"entity_ids": ["todo:a"], "intent": "full"},
    )
    assert result["supported"] == ["body", "card"]
