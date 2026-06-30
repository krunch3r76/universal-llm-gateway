"""Hermetic tests: _promote_session_objectives seeds recon-pending todos at session close.

Uses the migrated-schema SQLite fixture — no live services required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_store.dispatch_ops._session_objective_promote import (
    promote_session_objectives as _promote_session_objectives,
)
from cortex_store.dispatch_ops.ops_entities import _op_entity_get

_SESSION_ID = "2026-06-28-xyz"
_AGENT = "t"
_TODO_ID = "todo:obj-sample"


@pytest.fixture()
def objective_db(migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind cortex_conn to the migrated test DB and suppress event recording."""
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._session_objective_promote.record",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._recon_seed.logger",
        __import__("logging").getLogger("test-recon-seed"),
    )


@pytest.mark.offline
def test_promote_objective_returns_created(objective_db: None) -> None:
    """_promote_session_objectives returns [{"todo_created": todo_id}] on success."""
    result = _promote_session_objectives(
        [
            {
                "slug": "obj-sample",
                "name": "Capture X",
                "required_skills": ["ulg-architecture"],
            }
        ],
        session_id=_SESSION_ID,
        agent=_AGENT,
    )
    assert result == [{"todo_created": _TODO_ID}]


@pytest.mark.offline
def test_promoted_todo_attributes(objective_db: None) -> None:
    """Auto-created todo has correct source_uri, required_skills, seed_contract_ack, density_triage."""
    _promote_session_objectives(
        [
            {
                "slug": "obj-sample",
                "name": "Capture X",
                "required_skills": ["ulg-architecture"],
            }
        ],
        session_id=_SESSION_ID,
        agent=_AGENT,
    )

    entity = _op_entity_get(entity_id=_TODO_ID, intent="full")
    assert "error" not in entity, f"todo not found: {entity}"
    assert entity["source_uri"] == "tasks/specs/obj-sample.md"

    attrs = entity.get("attributes") or {}
    assert attrs.get("required_skills") == ["ulg-architecture"]
    assert attrs.get("seed_contract_ack")
    assert attrs.get("density_triage") == "recon_pending"


@pytest.mark.offline
def test_promote_none_and_empty_are_noop(objective_db: None) -> None:
    """_promote_session_objectives(None, ...) and ([], ...) both return [] without error."""
    assert _promote_session_objectives(None, session_id=_SESSION_ID, agent=_AGENT) == []
    assert _promote_session_objectives([], session_id=_SESSION_ID, agent=_AGENT) == []


@pytest.mark.offline
def test_malformed_spec_reports_drop(objective_db: None) -> None:
    """A spec missing 'name' yields an explicit drop diagnostic, not a silent skip.

    P4: a casing typo ("Name" vs "name") would otherwise evaporate the objective
    on a success-reporting close. The valid spec still promotes.
    """
    result = _promote_session_objectives(
        [
            {"slug": "no-name-spec"},
            {"slug": "typo-spec", "Name": "Wrong Case"},
            {
                "slug": "obj-sample",
                "name": "Capture X",
                "required_skills": ["ulg-architecture"],
            },
        ],
        session_id=_SESSION_ID,
        agent=_AGENT,
    )
    assert {"todo_created": _TODO_ID} in result
    drops = [r["dropped"] for r in result if "dropped" in r]
    assert len(drops) == 2
    assert all("name" in d.get("missing", []) for d in drops)
