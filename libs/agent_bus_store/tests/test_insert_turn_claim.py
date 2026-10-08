"""Claim-path edges for idempotent turn 1."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from agent_bus_store.db import connection as connection_mod
from agent_bus_store.db import init_db, insert_turn
from agent_bus_store.db.turns import FirstTurnAuthorConflict


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()


def test_claim_rejects_turn_one_from_other_agent(bus_db) -> None:
    insert_turn(
        thread="1",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="s",
        body="stored",
        idempotent_first_turn=True,
    )
    with pytest.raises(FirstTurnAuthorConflict) as exc:
        insert_turn(
            thread="1",
            from_agent="web-anthropic",
            to_agent="cursor",
            subject="s",
            body="other",
            idempotent_first_turn=True,
        )
    assert exc.value.existing_from == "cursor"


def test_integrity_error_fallback_returns_existing_id(
    bus_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    winner, _, _, claimed = insert_turn(
        thread="1",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="s",
        body="stored",
        idempotent_first_turn=True,
    )
    assert claimed is False

    class _Empty:
        def fetchone(self):
            return None

    class _Proxy:
        def __init__(self, conn) -> None:
            self._conn = conn
            self._hid_claim = False

        def execute(self, sql, *args, **kwargs):
            if (
                isinstance(sql, str)
                and "turn_number = 1" in sql
                and sql.lstrip().upper().startswith("SELECT")
                and not self._hid_claim
            ):
                self._hid_claim = True
                return _Empty()
            return self._conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    @contextmanager
    def _hiding_write():
        with connection_mod.write_connect() as conn:
            yield _Proxy(conn)

    monkeypatch.setattr("agent_bus_store.db.turns.write_connect", _hiding_write)
    got, _, _, claimed_existing = insert_turn(
        thread="1",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="other",
        body="other",
        idempotent_first_turn=True,
    )
    assert got == winner
    assert claimed_existing is True
