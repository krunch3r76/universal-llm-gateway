"""Dispatch telemetry for entity_get intent and served row counts."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
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


def _capture_records(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    captured: list[tuple[str, dict]] = []

    def _fake_record(signal: str, **payload: object) -> None:
        captured.append((signal, dict(payload)))

    monkeypatch.setattr("cortex_store.dispatch_ops.record", _fake_record)
    return captured


def _seed_entity_with_assertions(conn: sqlite3.Connection, entity_id: str) -> None:
    insert_entity(conn, entity_id=entity_id, entity_type="todo")
    active_id = insert_assertion(
        conn, entity_id=entity_id, claim="Active claim for telemetry probe."
    )
    insert_assertion(
        conn,
        entity_id=entity_id,
        claim="Superseded claim for telemetry probe.",
        superseded_by=active_id,
    )


def _dispatch_events(captured: list[tuple[str, dict]]) -> list[dict]:
    return [payload for signal, payload in captured if signal == "mcp.cortex.dispatch"]


def _served_events(captured: list[tuple[str, dict]]) -> list[dict]:
    return [
        payload
        for signal, payload in captured
        if signal == "mcp.cortex.entity_get.served"
    ]


@pytest.mark.offline
def test_entity_get_card_emits_intent_and_served_counts(
    monkeypatch: pytest.MonkeyPatch,
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "todo:telemetry-card"
    _seed_entity_with_assertions(migrated_conn, entity_id)
    captured = _capture_records(monkeypatch)

    with _patched_conn(migrated_conn):
        result = execute_op("entity_get", {"entity_id": entity_id, "intent": "card"})

    assert "error" not in result
    dispatch = _dispatch_events(captured)
    assert len(dispatch) == 1
    assert dispatch[0]["tool"] == "entity_get"
    assert dispatch[0]["intent"] == "card"

    served = _served_events(captured)
    assert len(served) == 1
    assert served[0]["intent"] == "card"
    assert served[0]["entity_id"] == entity_id
    assert served[0]["active_count"] is not None
    assert served[0]["superseded_count"] is not None


@pytest.mark.offline
def test_entity_get_default_intent_is_card(
    monkeypatch: pytest.MonkeyPatch,
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "todo:telemetry-default-card"
    _seed_entity_with_assertions(migrated_conn, entity_id)
    captured = _capture_records(monkeypatch)

    with _patched_conn(migrated_conn):
        result = execute_op("entity_get", {"entity_id": entity_id})

    assert "error" not in result
    assert result.get("intent") == "card"
    assert "assertion_counts" in result
    assert "top_k_assertions" in result

    served = _served_events(captured)
    assert served[0]["intent"] == "card"
    assert served[0]["active_count"] == (
        result.get("assertion_counts") or {}
    ).get("active")
    assert served[0]["superseded_count"] == (
        result.get("assertion_counts") or {}
    ).get("superseded")


@pytest.mark.offline
def test_non_entity_get_dispatch_omits_intent(
    monkeypatch: pytest.MonkeyPatch,
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "decision:telemetry-non-entity-get"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="decision")
    insert_assertion(
        migrated_conn,
        entity_id=entity_id,
        claim="Confirmed for assertion_state probe.",
        confidence="confirmed",
    )
    captured = _capture_records(monkeypatch)

    class _Ctx:
        def __enter__(self) -> sqlite3.Connection:
            return migrated_conn

        def __exit__(self, *a: object) -> None:
            return None

    with patch("cortex_store.dispatch_ops.ops_assertions.cortex_conn", _Ctx):
        result = execute_op("assertion_state", {"entity_id": entity_id})

    assert "error" not in result
    dispatch = _dispatch_events(captured)
    assert len(dispatch) == 1
    assert dispatch[0]["tool"] == "assertion_state"
    assert "intent" not in dispatch[0]
    assert _served_events(captured) == []
