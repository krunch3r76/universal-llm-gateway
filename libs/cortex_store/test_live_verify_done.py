"""Done-transition live-verify gate — flagged todos only."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import HTTPException

from cortex_store.entity_crud import update_entity_impl
from cortex_store.workflow_state import (
    LAND_SHA_NOT_LIVE,
    LIVE_DEFECT,
    RULING_MISSING,
    VERDICT_MISSING,
    live_verify_close_refusal,
)

pytestmark = pytest.mark.offline


def _line(verdict: str) -> dict:
    return {
        "line": "(e) close succeeds",
        "probe_call": {"method": "fleet_liveness"},
        "observed": {"answer": "yes"},
        "verdict": verdict,
    }


def _relations(relation: str = "equal", answer: str = "yes") -> list[dict]:
    return [{"service": "cortex-api", "relation": relation, "answer": answer}]


def _verdict(
    lines: list[dict],
    *,
    relations: list[dict] | None = None,
    ruling: str | None = None,
) -> dict:
    attrs: dict = {
        "kind": "live_verify",
        "land_sha": "abc",
        "lines": lines,
        "service_relations": _relations() if relations is None else relations,
    }
    if ruling is not None:
        attrs["operator_ruling"] = ruling
    return {
        "id": 1,
        "entity_id": "todo:flagged",
        "claim": "live_verify",
        "attributes": attrs,
        "superseded_by": None,
    }


def _flagged() -> dict:
    return {"live_verify_required": True}


def test_missing_verdict_refused() -> None:
    assert live_verify_close_refusal(_flagged(), []) == VERDICT_MISSING


def test_live_defect_refused() -> None:
    row = _verdict([_line("LIVE_DEFECT")])
    assert live_verify_close_refusal(_flagged(), [row]) == LIVE_DEFECT


def test_bad_relation_refused() -> None:
    row = _verdict([_line("LIVE_OK")], relations=_relations("descendant", "no"))
    assert live_verify_close_refusal(_flagged(), [row]) == LAND_SHA_NOT_LIVE


def test_unknown_answer_folds_into_land_sha() -> None:
    row = _verdict([_line("LIVE_OK")], relations=_relations(None, "unknown"))  # type: ignore[arg-type]
    assert live_verify_close_refusal(_flagged(), [row]) == LAND_SHA_NOT_LIVE


def test_live_ok_equal_or_ancestor_passes() -> None:
    equal = _verdict([_line("LIVE_OK")], relations=_relations("equal", "yes"))
    ancestor = _verdict([_line("LIVE_OK")], relations=_relations("ancestor", "yes"))
    assert live_verify_close_refusal(_flagged(), [equal]) is None
    assert live_verify_close_refusal(_flagged(), [ancestor]) is None


def test_untestable_without_ruling_refused() -> None:
    row = _verdict([_line("LIVE_UNTESTABLE")])
    assert live_verify_close_refusal(_flagged(), [row]) == RULING_MISSING


def test_same_close_passes_after_superseding_ruling() -> None:
    old = _verdict([_line("LIVE_UNTESTABLE")])
    old["superseded_by"] = 2
    new = _verdict([_line("LIVE_UNTESTABLE")], ruling="operator accepts untestable")
    new["id"] = 2
    assert live_verify_close_refusal(_flagged(), [old, new]) is None


def test_unflagged_todo_closes() -> None:
    assert live_verify_close_refusal({}, []) is None
    assert live_verify_close_refusal({"live_verify_required": False}, []) is None


def test_superseded_only_row_is_missing() -> None:
    row = _verdict([_line("LIVE_OK")])
    row["superseded_by"] = 9
    assert live_verify_close_refusal(_flagged(), [row]) == VERDICT_MISSING


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT,
            name TEXT,
            description TEXT,
            workflow_state TEXT,
            attributes TEXT,
            lifecycle TEXT,
            confidence_band TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY,
            entity_id TEXT,
            claim TEXT,
            attributes TEXT,
            superseded_by INTEGER
        )
        """
    )
    return conn


def test_flagged_update_does_not_write_done() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, workflow_state, attributes, created_at) "
        "VALUES ('todo:flagged', 'todo', 'Flagged', 'open', ?, '2026-10-07T00:00:00Z')",
        (json.dumps({"live_verify_required": True}),),
    )
    with pytest.raises(HTTPException) as caught:
        update_entity_impl(
            conn,
            entity_id="todo:flagged",
            updates={"workflow_state": "done"},
            commit=False,
        )
    assert caught.value.detail == VERDICT_MISSING
    state = conn.execute(
        "SELECT workflow_state FROM entities WHERE id = 'todo:flagged'"
    ).fetchone()
    assert state["workflow_state"] == "open"


def test_unflagged_update_reaches_done() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, workflow_state, attributes, created_at) "
        "VALUES ('todo:plain', 'todo', 'Plain', 'open', '{}', '2026-10-07T00:00:00Z')"
    )
    update_entity_impl(
        conn,
        entity_id="todo:plain",
        updates={"workflow_state": "done"},
        commit=False,
    )
    state = conn.execute(
        "SELECT workflow_state FROM entities WHERE id = 'todo:plain'"
    ).fetchone()
    assert state["workflow_state"] == "done"
