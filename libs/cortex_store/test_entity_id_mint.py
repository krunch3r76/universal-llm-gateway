"""Fork 2 acceptance tests — entity private id + mutable primary name (AC-1..8)."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

import pytest
from fastapi import HTTPException

from cortex_store.entity_aliases import resolve_entity_reference
from cortex_store.entity_crud import create_entity_impl, update_entity_impl
from cortex_store.entity_id_mint import is_minted_local_slug


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    return migrated_conn


@pytest.fixture()
def recorded_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(signal: str, **payload: Any) -> None:
        events.append((signal, payload))

    monkeypatch.setattr("cortex_store.event_publisher.record", _capture)
    return events


def test_ac1_minted_person_create_without_id(
    conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    result = create_entity_impl(
        conn,
        {"type": "person", "name": "Kaywan"},
    )
    entity_id = str(result["id"])
    assert re.fullmatch(r"person:[0-9a-hjkmnp-tv-z]{26}", entity_id)
    assert result["name"] == "Kaywan"
    slug = entity_id.split(":", 1)[1]
    assert is_minted_local_slug(slug)

    rows = conn.execute(
        "SELECT entity_id, entity_type, alias FROM entity_aliases WHERE entity_id = ?",
        (entity_id,),
    ).fetchall()
    assert ("Kaywan",) in {(r[2],) for r in rows}
    assert any(r[1] == "person" and r[2] == "Kaywan" for r in rows)

    mint_events = [e for e in recorded_events if e[0] == "cortex.entity.id.minted"]
    assert len(mint_events) == 1
    assert mint_events[0][1]["entity_id"] == entity_id
    assert mint_events[0][1]["mint"] == "ulid"


def test_ac2_minted_type_rejects_supplied_id(
    conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        create_entity_impl(
            conn,
            {
                "type": "organization",
                "id": "organization:petalco",
                "name": "Patelco",
            },
        )
    exc = exc_info.value
    assert exc.status_code == 422
    detail = exc.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "entity_id_minted_type"
    assert detail.get("retryable") is False
    assert "data" in detail and "fix" in detail["data"]

    count = conn.execute(
        "SELECT COUNT(*) FROM entities WHERE id LIKE 'organization:%'"
    ).fetchone()[0]
    assert count == 0

    rejected = [
        e for e in recorded_events if e[0] == "cortex.entity.create.id_rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0][1]["supplied_id"] == "organization:petalco"


def test_ac3_designated_todo_create_unchanged(conn: sqlite3.Connection) -> None:
    result = create_entity_impl(
        conn,
        {"type": "todo", "id": "todo:x", "name": "X", "attributes": {"density_triage": "mechanical"}},
    )
    assert result["id"] == "todo:x"
    assert result["name"] == "X"
    assert result["type"] == "todo"


def test_ac4_designated_todo_requires_id(conn: sqlite3.Connection) -> None:
    with pytest.raises(HTTPException) as exc_info:
        create_entity_impl(
            conn,
            {"type": "todo", "name": "X", "attributes": {"density_triage": "mechanical"}},
        )
    exc = exc_info.value
    assert exc.status_code == 422
    detail = exc.detail
    assert isinstance(detail, dict)
    assert detail.get("field") == "id" or detail.get("error") == "missing_required_fields"


def test_ac5_minted_name_update_retains_prior_alias(
    conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    created = create_entity_impl(
        conn,
        {"type": "organization", "name": "Patelco"},
    )
    entity_id = str(created["id"])
    recorded_events.clear()

    updated = update_entity_impl(
        conn,
        entity_id=entity_id,
        updates={"name": "Patelco Credit Union"},
    )
    assert updated["id"] == entity_id
    assert updated["name"] == "Patelco Credit Union"
    assert "Patelco" in (updated.get("aliases") or [])

    alias_rows = {
        r[2]
        for r in conn.execute(
            "SELECT entity_id, entity_type, alias FROM entity_aliases WHERE entity_id = ?",
            (entity_id,),
        ).fetchall()
    }
    assert {"Patelco Credit Union", "Patelco"}.issubset(alias_rows)

    name_events = [
        e for e in recorded_events if e[0] == "cortex.entity.name.changed"
    ]
    assert len(name_events) == 1
    assert name_events[0][1]["prior_name"] == "Patelco"
    assert name_events[0][1]["prior_name_retained"] is True


def test_ac6_resolve_by_name_and_exact_id(conn: sqlite3.Connection) -> None:
    created = create_entity_impl(
        conn,
        {"type": "organization", "name": "Patelco"},
    )
    entity_id = str(created["id"])

    update_entity_impl(
        conn,
        entity_id=entity_id,
        updates={"name": "Patelco Credit Union"},
    )

    by_full_name = resolve_entity_reference(
        conn, "organization:Patelco Credit Union"
    )
    assert by_full_name.entity_id == entity_id
    assert by_full_name.resolved_alias is not None

    by_prior = resolve_entity_reference(conn, "organization:Patelco")
    assert by_prior.entity_id == entity_id

    by_id = resolve_entity_reference(conn, entity_id)
    assert by_id.entity_id == entity_id
    assert by_id.resolved_alias is None


def test_ac7_ambiguous_name_resolution(
    conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    first = create_entity_impl(
        conn,
        {"type": "person", "name": "John Smith"},
    )
    second = create_entity_impl(
        conn,
        {"type": "person", "name": "John Smith", "duplicate_name_ok": True},
    )
    recorded_events.clear()

    with pytest.raises(HTTPException) as exc_info:
        resolve_entity_reference(conn, "person:John Smith")
    exc = exc_info.value
    assert exc.status_code == 400
    detail = exc.detail
    assert isinstance(detail, dict)
    matches = detail.get("matches") or []
    match_ids = {str(m["entity_id"]) for m in matches}
    assert first["id"] in match_ids
    assert second["id"] in match_ids

    ambiguous = [
        e for e in recorded_events if e[0] == "cortex.entity.alias.ambiguous"
    ]
    assert len(ambiguous) == 1
    assert ambiguous[0][1]["match_count"] == 2


def test_ac8_duplicate_name_rejected_without_flag(
    conn: sqlite3.Connection,
    recorded_events: list[tuple[str, dict[str, Any]]],
) -> None:
    first = create_entity_impl(
        conn,
        {"type": "person", "name": "John Smith"},
    )
    recorded_events.clear()

    with pytest.raises(HTTPException) as exc_info:
        create_entity_impl(
            conn,
            {"type": "person", "name": "John Smith"},
        )
    exc = exc_info.value
    assert exc.status_code == 409
    detail = exc.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "entity_name_exists"
    assert detail["data"]["existing_entity_id"] == first["id"]

    dup_events = [
        e for e in recorded_events if e[0] == "cortex.entity.name.duplicate_rejected"
    ]
    assert len(dup_events) == 1
