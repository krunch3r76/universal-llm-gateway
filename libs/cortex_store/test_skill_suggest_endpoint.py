"""Tests for POST /skills/suggest — endpoint + Stage-A engine (spec §8 tests 1–4)."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cortex_store.routes.skills import post_skill_suggest

_SCHEMA = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    source_uri TEXT,
    lifecycle TEXT,
    attributes TEXT
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _insert(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    source_uri: str,
    trigger_match_terms: list[str] | str | None = None,
    trigger_short: str = "",
    applicable_agents: list[str] | None = None,
    boot_importance: str | None = None,
    delivery_priority: int = 100,
) -> None:
    attrs: dict[str, object] = {}
    if applicable_agents is not None:
        attrs["applicable_agents"] = applicable_agents
    if trigger_match_terms is not None:
        attrs["trigger_match_terms"] = trigger_match_terms
    if trigger_short:
        attrs["trigger_short"] = trigger_short
    if boot_importance:
        attrs["boot_importance"] = boot_importance
    attrs["delivery_priority"] = delivery_priority
    conn.execute(
        "INSERT INTO entities (id, type, name, source_uri, attributes) VALUES (?, 'agent_skill', ?, ?, ?)",
        (entity_id, entity_id.removeprefix("agent_skill:"), source_uri, json.dumps(attrs)),
    )


class _FakeRequest:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


async def _call(payload: dict, conn: sqlite3.Connection):
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        with patch("cortex_store.routes.skills.cortex_conn", return_value=conn):
            return await post_skill_suggest(_FakeRequest(payload))


@pytest.fixture()
def suggest_conn() -> sqlite3.Connection:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:consult-routing",
        source_uri="agent-skills/consult-routing.md",
        trigger_match_terms=["consult", "handoff"],
        applicable_agents=["claude-web"],
    )
    _insert(
        conn,
        "agent_skill:session-close",
        source_uri="agent-skills/session-close.md",
        trigger_match_terms=["session", "close"],
        applicable_agents=["claude-web"],
        boot_importance="required_gate",
        delivery_priority=10,
    )
    _insert(
        conn,
        "agent_skill:cursor-only",
        source_uri="agent-skills/cursor-only.md",
        trigger_match_terms=["consult"],
        applicable_agents=["claude-cursor"],
    )
    _insert(
        conn,
        "agent_skill:no-agents",
        source_uri="agent-skills/no-agents.md",
        trigger_match_terms=["consult"],
        applicable_agents=None,
    )
    conn.commit()
    return conn


@pytest.mark.asyncio
async def test_delta_correctness(suggest_conn: sqlite3.Connection) -> None:
    body = await _call(
        {
            "agent": "claude-web",
            "loaded": [],
            "conversation_context": "need consult handoff help",
        },
        suggest_conn,
    )
    slugs = {s["slug"] for s in body["suggestions"]}
    assert "consult-routing" in slugs


@pytest.mark.parametrize(
    "loaded",
    [
        ["consult-routing"],
        ["agent_skill:consult-routing"],
        ["consult-routing.md"],
    ],
)
@pytest.mark.asyncio
async def test_loaded_delta_forms(
    suggest_conn: sqlite3.Connection, loaded: list[str]
) -> None:
    body = await _call(
        {
            "agent": "claude-web",
            "loaded": loaded,
            "conversation_context": "consult handoff routing",
        },
        suggest_conn,
    )
    slugs = {s["slug"] for s in body["suggestions"]}
    assert "consult-routing" not in slugs
    assert any(o["slug"] == "consult-routing" for o in body["omitted"])
    assert "consult-routing" in body["loaded_echo"]


@pytest.mark.asyncio
async def test_seat_gate_wrong_seat(suggest_conn: sqlite3.Connection) -> None:
    body = await _call(
        {
            "agent": "claude-web",
            "loaded": [],
            "conversation_context": "consult handoff",
        },
        suggest_conn,
    )
    assert all(s["slug"] != "cursor-only" for s in body["suggestions"])


@pytest.mark.asyncio
async def test_seat_gate_null_applicable_agents(suggest_conn: sqlite3.Connection) -> None:
    body = await _call(
        {
            "agent": "claude-web",
            "loaded": [],
            "conversation_context": "consult routing",
        },
        suggest_conn,
    )
    assert all(s["slug"] != "no-agents" for s in body["suggestions"])


@pytest.mark.asyncio
async def test_ranking_required_gate_first(suggest_conn: sqlite3.Connection) -> None:
    body = await _call(
        {
            "agent": "claude-web",
            "loaded": [],
            "conversation_context": "session close consult handoff",
            "limit": 2,
        },
        suggest_conn,
    )
    slugs = [s["slug"] for s in body["suggestions"]]
    assert slugs[0] == "session-close"


@pytest.mark.asyncio
async def test_metadata_coverage_dynamic(suggest_conn: sqlite3.Connection) -> None:
    rows = suggest_conn.execute(
        "SELECT COUNT(*) AS c FROM entities WHERE type='agent_skill'"
    ).fetchone()
    assert rows["c"] >= 4
    body = await _call(
        {
            "agent": "claude-web",
            "loaded": [],
            "conversation_context": "consult session close handoff",
        },
        suggest_conn,
    )
    assert body["count"] == len(body["suggestions"])
