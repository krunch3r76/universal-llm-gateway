"""Tests for session-close todo reconciliation preflight fields."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_store.conftest import bind_cortex_db
from cortex_store.dispatch_ops._session_todo_reconciliation import (
    open_todos_in_entity_ids,
    todo_reconciliation_preflight_fields,
)


def test_open_todos_in_entity_ids_empty_when_none() -> None:
    assert open_todos_in_entity_ids(None) == []
    assert open_todos_in_entity_ids([]) == []


def test_open_todos_in_entity_ids_ignores_non_todos() -> None:
    assert open_todos_in_entity_ids(["project:foo", "decision:bar"]) == []


def test_todo_reconciliation_preflight_fields_shape(
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    fields = todo_reconciliation_preflight_fields(["todo:missing-entity-xyz"])
    assert "open_todos_in_entity_ids" in fields
    assert isinstance(fields["open_todos_in_entity_ids"], list)
