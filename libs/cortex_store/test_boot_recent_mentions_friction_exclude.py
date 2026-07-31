"""GET /boot-recent-mentions friction exclusion (todo:boot-recent-mentions-friction-exclude)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cortex_store.conftest import bind_cortex_db
from cortex_store.routes.boot.recent_mentions import get_boot_recent_mentions

_NOW = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
_OLD = "2020-01-01T00:00:00Z"


def _seed_entity(
    conn,
    *,
    entity_id: str,
    entity_type: str = "service",
    name: str | None = None,
    created_at: str = _NOW,
) -> None:
    conn.execute(
        """
        INSERT INTO entities (id, type, name, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entity_id, entity_type, name or entity_id, created_at, created_at),
    )
    conn.commit()


def _seed_assertion(
    conn,
    *,
    entity_id: str,
    claim: str,
    created_at: str = _NOW,
) -> None:
    conn.execute(
        """
        INSERT INTO assertions (entity_id, claim, confidence, derivation_type, created_at, updated_at)
        VALUES (?, ?, 'hypothesized', 'agent_observation', ?, ?)
        """,
        (entity_id, claim, created_at, created_at),
    )
    conn.commit()


@pytest.fixture()
def mentions_db(migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bind_cortex_db(monkeypatch, migrated_db_path)
    return migrated_db_path


def _entity_ids(payload: dict) -> set[str]:
    return {item["entity_id"] for item in payload["items"]}


def _call_recent_mentions(**overrides: object) -> dict:
    kwargs: dict[str, object] = {
        "days": 7,
        "limit": 10,
        "type_exclude": None,
        "include_compaction_pointers": False,
        "include_frictions": False,
    }
    kwargs.update(overrides)
    return get_boot_recent_mentions(**kwargs)  # type: ignore[arg-type]


@pytest.mark.offline
def test_excludes_friction_only_entity_by_default(mentions_db: Path) -> None:
    from cortex_store import db as cortex_db

    with cortex_db.cortex_conn() as conn:
        _seed_entity(conn, entity_id="service:friction-only", created_at=_OLD)
        _seed_assertion(
            conn,
            entity_id="service:friction-only",
            claim="[tool_error] boot card should not surface friction-only noise",
        )

    payload = _call_recent_mentions()
    assert "service:friction-only" not in _entity_ids(payload)
    assert payload["include_frictions"] is False


@pytest.mark.offline
def test_includes_entity_with_substantive_and_friction_activity(
    mentions_db: Path,
) -> None:
    from cortex_store import db as cortex_db

    with cortex_db.cortex_conn() as conn:
        _seed_entity(conn, entity_id="service:mixed-activity", created_at=_OLD)
        _seed_assertion(
            conn,
            entity_id="service:mixed-activity",
            claim="[tool_error] incidental friction on active entity",
        )
        _seed_assertion(
            conn,
            entity_id="service:mixed-activity",
            claim="Substantive session assertion on the same entity",
        )

    payload = _call_recent_mentions()
    by_id = {item["entity_id"]: item for item in payload["items"]}
    assert "service:mixed-activity" in by_id
    assert by_id["service:mixed-activity"]["inserted_count"] == 1
    assert by_id["service:mixed-activity"]["friction_count"] == 1


@pytest.mark.offline
def test_excludes_decision_friction_entity_by_default(mentions_db: Path) -> None:
    from cortex_store import db as cortex_db

    with cortex_db.cortex_conn() as conn:
        _seed_entity(
            conn,
            entity_id="decision:friction-21654",
            entity_type="decision",
            created_at=_OLD,
        )
        _seed_assertion(
            conn,
            entity_id="decision:friction-21654",
            claim="Decision record promoted from friction triage",
        )

    payload = _call_recent_mentions()
    assert "decision:friction-21654" not in _entity_ids(payload)


@pytest.mark.offline
def test_include_frictions_restores_friction_only_and_decision_entities(
    mentions_db: Path,
) -> None:
    from cortex_store import db as cortex_db

    with cortex_db.cortex_conn() as conn:
        _seed_entity(conn, entity_id="service:friction-only", created_at=_OLD)
        _seed_assertion(
            conn,
            entity_id="service:friction-only",
            claim="[protocol] friction-only entity for audit restore",
        )
        _seed_entity(
            conn,
            entity_id="decision:friction-audit",
            entity_type="decision",
            created_at=_OLD,
        )
        _seed_assertion(
            conn,
            entity_id="decision:friction-audit",
            claim="Friction decision surfaced when include_frictions=true",
        )

    payload = _call_recent_mentions(include_frictions=True)
    ids = _entity_ids(payload)
    assert ids >= {"service:friction-only", "decision:friction-audit"}
    by_id = {item["entity_id"]: item for item in payload["items"]}
    assert by_id["service:friction-only"]["inserted_count"] == 1
    assert by_id["service:friction-only"]["friction_count"] == 1
    assert payload["include_frictions"] is True
