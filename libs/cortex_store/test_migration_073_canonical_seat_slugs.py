"""Hermetic tests for migration 073 — reflective_journal agent canonicalization."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from agent_seat.registry import normalize_bus_address

_MIG073 = Path(__file__).parent / "migrations" / "073_canonical_seat_slugs.py"


def _load_migration(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


migration_073 = _load_migration(_MIG073, "migration_073")


def _seed_reflective(conn: sqlite3.Connection, agent: str, n: int = 1) -> None:
    for i in range(n):
        conn.execute(
            "INSERT INTO reflective_journal (agent, register, entry, kind) "
            "VALUES (?, ?, ?, ?)",
            (agent, "default", f"entry-{agent}-{i}", "entry"),
        )


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE reflective_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            register TEXT NOT NULL,
            entry TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'entry',
            session_id TEXT,
            revises INTEGER,
            consolidation_data TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
        """
    )
    return c


@pytest.mark.offline
def test_migration_collapses_web_variants(conn: sqlite3.Connection) -> None:
    _seed_reflective(conn, "web", 3)
    _seed_reflective(conn, "claude-web", 2)
    _seed_reflective(conn, "web-claude", 1)

    migration_073.migrate(conn)

    rows = conn.execute(
        "SELECT agent, COUNT(*) AS cnt FROM reflective_journal GROUP BY agent"
    ).fetchall()
    counts = {r["agent"]: r["cnt"] for r in rows}
    assert counts == {"web-anthropic": 6}


@pytest.mark.offline
def test_migration_preserves_distinct_unregistered_seats(
    conn: sqlite3.Connection,
) -> None:
    _seed_reflective(conn, "cursor-monitor-6661", 1)
    _seed_reflective(conn, "cursor-sdk", 1)

    migration_073.migrate(conn)

    rows = conn.execute(
        "SELECT agent, COUNT(*) AS cnt FROM reflective_journal GROUP BY agent ORDER BY agent"
    ).fetchall()
    counts = {r["agent"]: r["cnt"] for r in rows}
    assert counts == {"cursor-monitor-6661": 1, "cursor-sdk": 1}


@pytest.mark.offline
def test_migration_idempotent(conn: sqlite3.Connection) -> None:
    _seed_reflective(conn, "web", 2)
    migration_073.migrate(conn)
    first = conn.execute(
        "SELECT agent, COUNT(*) AS cnt FROM reflective_journal GROUP BY agent"
    ).fetchall()
    migration_073.migrate(conn)
    second = conn.execute(
        "SELECT agent, COUNT(*) AS cnt FROM reflective_journal GROUP BY agent"
    ).fetchall()
    assert [(r["agent"], r["cnt"]) for r in first] == [
        (r["agent"], r["cnt"]) for r in second
    ]


@pytest.mark.offline
def test_insert_chokepoint_normalizes_agent(conn: sqlite3.Connection) -> None:
    from cortex_store.routes.reflective_journal import _insert_reflective_entry_tx

    entry_id = _insert_reflective_entry_tx(
        conn,
        agent="claude-web",
        register="default",
        entry="test entry",
        kind="entry",
    )
    row = conn.execute(
        "SELECT agent FROM reflective_journal WHERE id = ?", (entry_id,)
    ).fetchone()
    assert row["agent"] == normalize_bus_address("claude-web")


@pytest.mark.offline
@pytest.mark.parametrize("raw_agent", ["web", "claude-web", "web-claude"])
def test_rj_write_persists_web_anthropic(
    cortex_client, raw_agent: str
) -> None:
    resp = cortex_client.post(
        "/reflective-journal",
        json={
            "agent": raw_agent,
            "register": "default",
            "entry": f"entry from {raw_agent}",
            "kind": "entry",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["agent"] == "web-anthropic"


@pytest.mark.offline
def test_boot_reflective_web_and_claude_web_same_results(cortex_client) -> None:
    for agent in ("web", "claude-web", "web-claude"):
        cortex_client.post(
            "/reflective-journal",
            json={
                "agent": agent,
                "register": "default",
                "entry": f"shared lane from {agent}",
                "kind": "entry",
            },
        )

    web_resp = cortex_client.get("/boot-reflective", params={"agent": "web"})
    claude_resp = cortex_client.get(
        "/boot-reflective", params={"agent": "claude-web"}
    )
    assert web_resp.status_code == 200
    assert claude_resp.status_code == 200
    assert web_resp.json()["total"] == claude_resp.json()["total"]
    assert web_resp.json()["total"] >= 3
    assert len(web_resp.json()["items"]) == len(claude_resp.json()["items"])


@pytest.mark.offline
def test_migration_packet_distribution_collapse(conn: sqlite3.Connection) -> None:
    """Simulates live DB shard counts from the implement packet."""
    distribution = {
        "web": 129,
        "claude-web": 116,
        "web-claude": 4,
        "web-anthropic": 2,
        "claude-web-lead": 1,
        "cursor-sdk": 5,
        "cursor-monitor-6661": 3,
    }
    for agent, count in distribution.items():
        _seed_reflective(conn, agent, count)

    before = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT agent, COUNT(*) FROM reflective_journal GROUP BY agent"
        ).fetchall()
    }
    migration_073.migrate(conn)
    after = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT agent, COUNT(*) FROM reflective_journal GROUP BY agent"
        ).fetchall()
    }
    assert before["web"] == 129
    assert after["web-anthropic"] == 251
    assert after.get("claude-web-lead") == 1
    assert after.get("cursor-sdk") == 5
    assert after.get("cursor-monitor-6661") == 3
    assert "web" not in after
    assert "claude-web" not in after

