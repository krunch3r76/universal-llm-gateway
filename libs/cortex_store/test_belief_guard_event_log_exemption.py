"""C2 event-log exemption (agent-bus:1195).

Conversation-turn archive assertions land on append-only ``thread:*`` anchor
entities at ``confidence="confirmed"``. A reused ``dispatch_thread_id`` replays
onto an anchor that already holds confirmed turns, and C2 hard-blocks (HTTP 409)
the moment two turns share a status-antonym pair. Turn records are immutable
events, not belief claims, so ``thread:*`` anchors are exempt from C2.

These assert:
- An antonym pair on an ordinary belief entity still hard-blocks (guard intact).
- The same pair on a ``thread:dispatch:*`` anchor is allowed (exemption).

Synthetic in-memory FTS only — no live cortex DB.
"""

from __future__ import annotations

import sqlite3

from .belief_guard import guard_assertion_write, is_event_log_entity

_SCHEMA = """
CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT, name TEXT);
CREATE TABLE assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT, claim TEXT, confidence TEXT, superseded_by INTEGER
);
CREATE VIRTUAL TABLE assertions_fts USING fts5(assertion_id UNINDEXED, indexed_text);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _seed_confirmed(conn: sqlite3.Connection, entity_id: str, claim: str) -> None:
    """Insert a confirmed assertion plus its FTS index row."""
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence) VALUES (?, ?, 'confirmed')",
        (entity_id, claim),
    )
    conn.execute(
        "INSERT INTO assertions_fts (assertion_id, indexed_text) VALUES (?, ?)",
        (cur.lastrowid, claim),
    )
    conn.commit()


def test_event_log_predicate_classifies_thread_anchors() -> None:
    assert is_event_log_entity("thread:dispatch:cursor-2026-06-02-boe")
    assert is_event_log_entity("thread:openai-chat:abc123")
    assert not is_event_log_entity("decision:foo")
    assert not is_event_log_entity("case:boe19p-flintridge-appeal-2026")


def test_antonym_pair_blocks_on_ordinary_entity() -> None:
    """Guard intact: confirmed antonym pair on a belief entity hard-blocks."""
    conn = _conn()
    eid = "decision:audit-posture"
    _seed_confirmed(conn, eid, "Citation audit is complete")

    result = guard_assertion_write(conn, eid, "Citation audit is incomplete")

    assert result.allowed is False
    assert result.block_detail is not None
    assert result.block_detail["error"] == "contradiction_detected"
    conn.close()


def test_antonym_pair_allowed_on_thread_anchor() -> None:
    """Exemption: same antonym pair on a reused dispatch anchor writes through."""
    conn = _conn()
    anchor = "thread:dispatch:cursor-2026-06-02-boe"
    _seed_confirmed(conn, anchor, "Assistant: Tier 2 residual risk is unresolved")

    result = guard_assertion_write(
        conn, anchor, "Assistant: Tier 2 residual risk is resolved"
    )

    assert result.allowed is True
    assert result.block_detail is None
    conn.close()


def test_openai_chat_anchor_also_exempt() -> None:
    """Exemption is caller-agnostic — covers the openai-chat compactor too."""
    conn = _conn()
    anchor = "thread:openai-chat:session-xyz"
    _seed_confirmed(conn, anchor, "User: is the gate open")

    result = guard_assertion_write(conn, anchor, "User: the gate is closed now")

    assert result.allowed is True
    conn.close()
