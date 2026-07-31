"""False-orphan demotion when late cursor-sdk terminal turns arrive."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_bus_store.db import (
    admit_dispatch,
    create_thread_with_turn,
    init_db,
)
from agent_bus_store.db.connection import connect
from agent_bus_store.db.turns import get_turns, insert_turn
from agent_bus_store.reconcile import reconcile_orphaned_dispatches


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def _seed_orphan_thread(
    bus_db,
    *,
    slug: str = "false-orphan",
    execution_id: str = "exec-false",
) -> str:
    thread_row, *_ = create_thread_with_turn(
        slug=slug,
        from_agent="dispatch",
        to_agent="claude-cursor",
        subject="cursor-sdk generate",
        body="pointer only",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="cursor-sdk-generate",
        caller_agent="claude-web",
    )
    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned"):
        reconcile_orphaned_dispatches()
    return thread_id


def _orphan_turns(thread_id: str) -> list[dict]:
    turns = get_turns(thread=thread_id, include_superseded=True)
    return [
        t
        for t in turns
        if t["from_agent"] == "dispatch"
        and "orphaned" in (t.get("subject") or "").lower()
    ]


def test_late_closeout_demotes_orphan(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db)
    orphan = _orphan_turns(thread_id)[0]
    assert orphan["status"] == "open"

    emitted: list[dict] = []
    with patch(
        "agent_bus_store.orphan_demote.emit_dispatch_orphan_demoted",
        side_effect=lambda **kwargs: emitted.append(kwargs),
    ):
        closeout_id, _, _ = insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="dispatch",
            subject="CLOSEOUT — task complete",
            body="done",
        )

    demoted = _orphan_turns(thread_id)[0]
    assert demoted["status"] == "superseded"
    assert demoted["supersedes_turn"] == closeout_id
    assert len(emitted) == 1
    assert emitted[0] == {
        "thread_id": thread_id,
        "orphan_turn_id": orphan["id"],
        "closeout_turn_id": closeout_id,
    }


def test_late_cursor_sdk_dispatch_demotes_orphan(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db, slug="dispatch-closeout")
    orphan_id = _orphan_turns(thread_id)[0]["id"]

    with patch("agent_bus_store.orphan_demote.emit_dispatch_orphan_demoted") as mock_emit:
        closeout_id, _, _ = insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="dispatch",
            subject="cursor-sdk dispatch disp-1 COMPLETED",
            body="result",
        )

    demoted = _orphan_turns(thread_id)[0]
    assert demoted["status"] == "superseded"
    assert demoted["supersedes_turn"] == closeout_id
    mock_emit.assert_called_once_with(
        thread_id=thread_id,
        orphan_turn_id=orphan_id,
        closeout_turn_id=closeout_id,
    )


def test_nonterminal_cursor_sdk_does_not_demote(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db, slug="nonterminal")

    with patch("agent_bus_store.orphan_demote.emit_dispatch_orphan_demoted") as mock_emit:
        insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="dispatch",
            subject="cursor-sdk progress update",
            body="still running",
        )

    orphan = _orphan_turns(thread_id)[0]
    assert orphan["status"] == "open"
    mock_emit.assert_not_called()


def test_true_orphan_without_late_terminal_stays_open(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db, slug="true-orphan")
    orphan = _orphan_turns(thread_id)[0]
    assert orphan["status"] == "open"


def test_second_terminal_idempotent(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db, slug="idem-demote")
    orphan_id = _orphan_turns(thread_id)[0]["id"]

    with patch("agent_bus_store.orphan_demote.emit_dispatch_orphan_demoted") as mock_emit:
        first_id, _, _ = insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="dispatch",
            subject="CLOSEOUT first",
            body="done",
        )
        second_id, _, _ = insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="dispatch",
            subject="CLOSEOUT second",
            body="also done",
        )

    demoted = _orphan_turns(thread_id)[0]
    assert demoted["status"] == "superseded"
    assert demoted["supersedes_turn"] == first_id
    mock_emit.assert_called_once_with(
        thread_id=thread_id,
        orphan_turn_id=orphan_id,
        closeout_turn_id=first_id,
    )
    assert second_id != first_id


def test_non_cursor_sdk_closeout_prose_does_not_demote(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db, slug="prose-closeout")

    with patch("agent_bus_store.orphan_demote.emit_dispatch_orphan_demoted") as mock_emit:
        insert_turn(
            thread=thread_id,
            from_agent="claude-web",
            to_agent="dispatch",
            subject="closeout summary for operator",
            body="manual closeout prose",
        )

    orphan = _orphan_turns(thread_id)[0]
    assert orphan["status"] == "open"
    mock_emit.assert_not_called()


def test_demotion_via_insert_turn_shared_choke(bus_db) -> None:
    """insert_turn is the shared choke for POST /turns and send paths."""
    thread_id = _seed_orphan_thread(bus_db, slug="choke")

    closeout_id, _, _ = insert_turn(
        thread=thread_id,
        from_agent="cursor-sdk",
        to_agent="dispatch",
        subject="cursor-sdk dispatch disp-choke COMPLETED",
        body="via insert_turn",
    )

    demoted = _orphan_turns(thread_id)[0]
    assert demoted["status"] == "superseded"
    assert demoted["supersedes_turn"] == closeout_id


def test_execution_id_mismatch_skips_demotion(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db, slug="exec-mismatch", execution_id="exec-a")
    orphan = _orphan_turns(thread_id)[0]
    assert "exec-a" in orphan["body"]

    with connect() as conn:
        conn.execute(
            "UPDATE thread_dispatch_links SET execution_id = ? WHERE thread_id = ?",
            ("exec-b", thread_id),
        )

    with patch("agent_bus_store.orphan_demote.emit_dispatch_orphan_demoted") as mock_emit:
        insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="dispatch",
            subject="CLOSEOUT late",
            body="done",
        )

    assert _orphan_turns(thread_id)[0]["status"] == "open"
    mock_emit.assert_not_called()
