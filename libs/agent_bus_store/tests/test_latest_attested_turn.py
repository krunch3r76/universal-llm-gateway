"""Latest attested turn is house+agent scoped and does not create a database."""

from __future__ import annotations

import pytest
from agent_bus_store.db import init_db
from agent_bus_store.db.turns import get_latest_attested_turn, insert_turn

pytestmark = pytest.mark.offline


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def test_unattested_head_and_other_agent_do_not_count(bus_db) -> None:
    """An unattested head and another agent's row do not count for this agent."""
    insert_turn(
        thread="9002",
        from_agent="orion",
        to_agent="cursor",
        subject="old",
        body="attested",
        attested_by="oauth:orion@code via=unknown(10.0.0.1)",
    )
    insert_turn(
        thread="9002",
        from_agent="cursor",
        to_agent="orion",
        subject="head",
        body="unattested head",
    )
    insert_turn(
        thread="9002",
        from_agent="other",
        to_agent="cursor",
        subject="other",
        body="someone else",
        attested_by="oauth:other@code",
    )
    row = get_latest_attested_turn("9002", "orion")
    assert row is not None
    assert row["from_agent"] == "orion"
    assert row["turn_number"] == 1
    assert row["attested_by"] == "oauth:orion@code via=unknown(10.0.0.1)"
    assert get_latest_attested_turn("9002", "cursor") is None


def test_blank_and_superseded_attestations_do_not_count(bus_db) -> None:
    """Blank attested_by and a superseded row are not the latest attestation."""
    insert_turn(
        thread="9001",
        from_agent="orion",
        to_agent="cursor",
        subject="blank",
        body="blank",
        attested_by="   ",
    )
    turn_id, _, _, _ = insert_turn(
        thread="9001",
        from_agent="orion",
        to_agent="cursor",
        subject="real",
        body="real",
        attested_by="oauth:orion@code",
    )
    insert_turn(
        thread="9001",
        from_agent="orion",
        to_agent="cursor",
        subject="head",
        body="unattested head supersedes the attestation",
        supersedes_turn=turn_id,
    )
    assert get_latest_attested_turn("9001", "orion") is None


def test_missing_database_raises_without_creating(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing database raises FileNotFoundError and does not create the file."""
    missing = tmp_path / "missing" / "messages.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(missing))
    with pytest.raises(FileNotFoundError):
        get_latest_attested_turn("21207", "orion")
    assert not missing.exists()
    assert not missing.parent.exists()
