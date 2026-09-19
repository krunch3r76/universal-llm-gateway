"""friction_close already_closed branch — losing-seat resolution_note echo + persist."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_store.dispatch_ops._friction_close_impl import close_friction_assertion
from cortex_store.dispatch_ops.ops_assertions_update import _op_assertion_get
from cortex_store.dispatch_ops.test_friction_to_todo import (
    _insert_friction,
    _patch_supersede_side_effects,
    _seed_skill_entity,
)

_NOTE_1900B = "x" * 1900


@pytest.fixture()
def superseded_friction(
    migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[int, int]:
    """Open friction pre-marked superseded; returns (friction_id, fulfillment_id)."""
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    _patch_supersede_side_effects(monkeypatch)

    with cortex_db.cortex_conn() as conn:
        _seed_skill_entity(conn)
        friction_id = _insert_friction(conn)
        fulfillment_id = _insert_friction(conn, "[resolved:wontfix] first closer")
        conn.execute(
            "UPDATE assertions SET superseded_by = ? WHERE id = ?",
            (fulfillment_id, friction_id),
        )
        conn.commit()
    return friction_id, fulfillment_id


@pytest.mark.offline
def test_already_closed_echoes_resolution_note_verbatim(
    superseded_friction: tuple[int, int],
) -> None:
    friction_id, fulfillment_id = superseded_friction
    result = close_friction_assertion(
        friction_id,
        "wontfix",
        agent="losing-seat",
        session_id="test",
        resolution_note=_NOTE_1900B,
    )
    assert result.get("status") == "already_closed"
    assert result.get("fulfillment_assertion_id") == fulfillment_id
    assert result.get("resolution_note") == _NOTE_1900B
    assert isinstance(result.get("note_persisted"), int)


@pytest.mark.offline
def test_already_closed_persists_note_on_fulfillment_chain(
    superseded_friction: tuple[int, int],
) -> None:
    friction_id, fulfillment_id = superseded_friction
    note = "independent per-defect disposition from losing seat"
    result = close_friction_assertion(
        friction_id,
        "wontfix",
        agent="losing-seat",
        session_id="test",
        resolution_note=note,
    )
    note_id = result.get("note_persisted")
    assert isinstance(note_id, int)

    stored = _op_assertion_get(assertion_id=note_id)
    item = stored.get("item") or stored
    assert item["claim"] == note
    assert item["fulfillment_assertion_id"] == fulfillment_id
    attrs = item.get("attributes") or {}
    if isinstance(attrs, str):
        attrs = json.loads(attrs)
    assert attrs.get("friction_assertion_id") == friction_id


@pytest.mark.offline
def test_already_closed_omits_note_when_absent(
    superseded_friction: tuple[int, int],
) -> None:
    friction_id, _fulfillment_id = superseded_friction
    result = close_friction_assertion(
        friction_id,
        "wontfix",
        agent="losing-seat",
        session_id="test",
    )
    assert result.get("status") == "already_closed"
    assert "resolution_note" not in result
    assert "note_persisted" not in result
    assert "note_persist_error" not in result


@pytest.mark.offline
def test_already_closed_omits_note_when_empty_string(
    superseded_friction: tuple[int, int],
) -> None:
    friction_id, _fulfillment_id = superseded_friction
    result = close_friction_assertion(
        friction_id,
        "wontfix",
        agent="losing-seat",
        session_id="test",
        resolution_note="",
    )
    assert result.get("status") == "already_closed"
    assert "resolution_note" not in result
    assert "note_persisted" not in result


@pytest.mark.offline
def test_already_closed_persist_failure_still_echoes_note(
    superseded_friction: tuple[int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    friction_id, _fulfillment_id = superseded_friction
    note = "must survive persistence failure"

    def _fail_create(_body: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(
        "cortex_store.dispatch_ops._friction_close_impl._create_assertion_impl",
        _fail_create,
    )

    result = close_friction_assertion(
        friction_id,
        "wontfix",
        agent="losing-seat",
        session_id="test",
        resolution_note=note,
    )
    assert result.get("status") == "already_closed"
    assert result.get("resolution_note") == note
    assert result.get("note_persisted") is None
    assert "simulated write failure" in str(result.get("note_persist_error"))
