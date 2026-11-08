"""Bus-backed transcript tail when no sdk run-line log exists."""

from __future__ import annotations

import sqlite3

import pytest

from services.git_integration_worker.cursor_sdk_run_lines import (
    _bus_turns_sqlite,
    conversation_tail,
)


def test_bus_turns_sqlite_reads_after_turn(tmp_path, monkeypatch) -> None:
    db = tmp_path / "bus.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE turns (turn_number INT, from_agent TEXT, to_agent TEXT, "
        "subject TEXT, body TEXT, thread TEXT)"
    )
    conn.execute(
        "INSERT INTO turns VALUES (1,'a','b','hi','body1','42'),"
        "(2,'c','d','ok','body2','42')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db))
    rows = _bus_turns_sqlite("42", 1)
    assert len(rows) == 1
    assert rows[0]["turn_number"] == 2


def test_conversation_tail_falls_back_to_bus(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "bus.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE turns (turn_number INT, from_agent TEXT, to_agent TEXT, "
        "subject TEXT, body TEXT, thread TEXT)"
    )
    conn.execute(
        "INSERT INTO turns VALUES (1,'synthesizer','ide','reply','hello','99')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db))
    monkeypatch.setenv("AGENT_BUS_TOKEN", "")

    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    led = CursorDispatchLedger.instance()
    with led._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, resolved_model, status, "
            "message_present, read_only, record_json) "
            "VALUES ('tail-bus-test', 'fp', '99', 'anthropic/claude', 'admitted', 0, 0, '{}')"
        )
        conn.commit()

    payload = conversation_tail("tail-bus-test", 0)
    assert payload["source"] == "agent-bus.turns"
    assert payload["lines"]
    assert "hello" in payload["lines"][0]["text"]
