"""Hop-before-checkpoint precedence (friction:43744 S2)."""

from __future__ import annotations

import pytest
from agent_bus_store.db import init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.hop_precedence import get_hop_precedence
from agent_bus_store.db.lifecycle_fold import (
    HELD_CHECKPOINT_PIN_MISMATCH,
    HELD_CHECKPOINT_REF_MISSING,
    HELD_HOP_BEFORE_CHECKPOINT,
    HOP_STATUS_PENDING,
    HOP_STATUS_READY,
    empty_hop_fold_state,
    fold_hop_precedence,
)
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient

_URI = "cortex://notes/system/threads/example-cp.md"
_PIN = "abc123deadbeef" * 4  # 64 hex chars
_PIN_OTHER = "fedcba9876543210" * 4


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()


def _hop_body(*, uri: str = _URI, pin: str = _PIN) -> str:
    return f"required_checkpoint: {uri}\nread_sha256: {pin}\n"


def _cp_body(*, uri: str = _URI, pin: str = _PIN, cp_ordinal: int = 1) -> str:
    return f"uri: {uri}\nread_sha256: {pin}\ncp_ordinal: {cp_ordinal}\n"


def _single_hop_row(thread: str) -> dict:
    with connect() as conn:
        rows = get_hop_precedence(conn, thread_id=thread)
    assert len(rows) == 1
    return rows[0]


def test_checkpoint_seal_preserves_hop_work_key(bus_db) -> None:
    """F1: ready flip via CHECKPOINT must not NULL out the hop row's work_key."""
    work_key = "friction:43744"
    insert_turn(
        thread="99010",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="HOP|liaison",
        body=_hop_body() + f"work_key: {work_key}\n",
    )
    with connect() as conn:
        rows = get_hop_precedence(conn, work_key=work_key)
    assert len(rows) == 1
    assert rows[0]["hop_status"] == HOP_STATUS_PENDING

    insert_turn(
        thread="99010",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="CHECKPOINT|wave",
        body=_cp_body(),
    )
    with connect() as conn:
        by_key = get_hop_precedence(conn, work_key=work_key)
    assert len(by_key) == 1
    assert by_key[0]["hop_status"] == HOP_STATUS_READY
    assert by_key[0]["work_key"] == work_key

    app = create_app()
    client = TestClient(app)
    response = client.get("/lifecycle/hop-precedence", params={"work_key": work_key})
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["hop_status"] == HOP_STATUS_READY
    assert items[0]["work_key"] == work_key


def test_hop_before_checkpoint_pending_then_ready(bus_db) -> None:
    insert_turn(
        thread="99001",
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="HOP|liaison",
        body=_hop_body(),
    )
    row = _single_hop_row("99001")
    assert row["hop_status"] == HOP_STATUS_PENDING
    assert row["held_reason"] == HELD_HOP_BEFORE_CHECKPOINT

    insert_turn(
        thread="99001",
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="CHECKPOINT|wave",
        body=_cp_body(),
    )
    row = _single_hop_row("99001")
    assert row["hop_status"] == HOP_STATUS_READY
    assert row["held_reason"] is None
    assert row["checkpoint_read_sha256"] == _PIN


def test_checkpoint_then_hop_immediate_ready(bus_db) -> None:
    insert_turn(
        thread="99002",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="CHECKPOINT|wave",
        body=_cp_body(),
    )
    insert_turn(
        thread="99002",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="HOP|liaison",
        body=_hop_body(),
    )
    row = _single_hop_row("99002")
    assert row["hop_status"] == HOP_STATUS_READY
    assert row["held_reason"] is None


def test_pin_mismatch_stays_pending(bus_db) -> None:
    insert_turn(
        thread="99003",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="CHECKPOINT|wave",
        body=_cp_body(pin=_PIN),
    )
    insert_turn(
        thread="99003",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="HOP|liaison",
        body=_hop_body(pin=_PIN_OTHER),
    )
    row = _single_hop_row("99003")
    assert row["hop_status"] == HOP_STATUS_PENDING
    assert row["held_reason"] == HELD_CHECKPOINT_PIN_MISMATCH


def test_hop_without_ref_checkpoint_ref_missing(bus_db) -> None:
    insert_turn(
        thread="99004",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="HOP|liaison",
        body="hop without structured ref\n",
    )
    row = _single_hop_row("99004")
    assert row["hop_status"] == HOP_STATUS_PENDING
    assert row["held_reason"] == HELD_CHECKPOINT_REF_MISSING


def test_hope_subject_does_not_classify_as_hop(bus_db) -> None:
    insert_turn(
        thread="99005",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="HOPE|not-a-hop",
        body=_hop_body(),
    )
    with connect() as conn:
        rows = get_hop_precedence(conn, thread_id="99005")
    assert rows == []


def test_checkpoints_plural_subject_not_sealed(bus_db) -> None:
    insert_turn(
        thread="99006",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="CHECKPOINTS|batch",
        body=_cp_body(),
    )
    insert_turn(
        thread="99006",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="HOP|liaison",
        body=_hop_body(),
    )
    row = _single_hop_row("99006")
    assert row["hop_status"] == HOP_STATUS_PENDING
    assert row["held_reason"] == HELD_HOP_BEFORE_CHECKPOINT


def test_fold_pure_hop_before_checkpoint() -> None:
    state = empty_hop_fold_state()
    state = fold_hop_precedence(
        state,
        {
            "event_type": "hop.requested",
            "refs": {
                "turn_id": 10,
                "required_checkpoint_uri": _URI,
                "required_read_sha256": _PIN,
            },
        },
    )
    hop = state["hops"]["10"]
    assert hop["hop_status"] == HOP_STATUS_PENDING
    assert hop["held_reason"] == HELD_HOP_BEFORE_CHECKPOINT

    state = fold_hop_precedence(
        state,
        {
            "event_type": "checkpoint.sealed",
            "refs": {
                "turn_id": 3,
                "uri": _URI,
                "read_sha256": _PIN,
                "cp_ordinal": 1,
            },
        },
    )
    hop = state["hops"]["10"]
    assert hop["hop_status"] == HOP_STATUS_READY


def test_fold_pure_pin_mismatch() -> None:
    state = empty_hop_fold_state()
    state = fold_hop_precedence(
        state,
        {
            "event_type": "checkpoint.sealed",
            "refs": {
                "turn_id": 1,
                "uri": _URI,
                "read_sha256": _PIN,
                "cp_ordinal": 1,
            },
        },
    )
    state = fold_hop_precedence(
        state,
        {
            "event_type": "hop.requested",
            "refs": {
                "turn_id": 2,
                "required_checkpoint_uri": _URI,
                "required_read_sha256": _PIN_OTHER,
            },
        },
    )
    hop = state["hops"]["2"]
    assert hop["held_reason"] == HELD_CHECKPOINT_PIN_MISMATCH
