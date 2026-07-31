"""Dispatch-path tests for skill_suggest LLM reasoning default (AC14)."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
STARGATE_SYSTEMS = ROOT / "services" / "universal-stargate"
if str(STARGATE_SYSTEMS) not in sys.path:
    sys.path.insert(0, str(STARGATE_SYSTEMS))

# ruff: noqa: I001
from cortex_store.routes._skill_suggest import run_stage_a  # noqa: E402
from systems.frontier_consult.admission import FrontierEndpointError  # noqa: E402
from systems.frontier_consult.skill_suggest_dispatch import (  # noqa: E402
    SkillSuggestDispatchRequest,
    dispatch_skill_suggest,
)
from systems.frontier_consult.skill_suggest_dispatch_helpers import (  # noqa: E402
    build_worker_message,
)
from systems.frontier_consult.skill_suggest_worker_waiter import WorkerWaitOutcome  # noqa: E402

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
    trigger_match_terms: list[str] | None = None,
    applicable_agents: list[str] | None = None,
) -> None:
    attrs: dict[str, object] = {
        "applicable_agents": applicable_agents or ["claude-cursor"]
    }
    if trigger_match_terms is not None:
        attrs["trigger_match_terms"] = trigger_match_terms
    conn.execute(
        "INSERT INTO entities (id, type, name, source_uri, lifecycle, attributes) "
        "VALUES (?, 'agent_skill', ?, ?, 'active', ?)",
        (
            entity_id,
            entity_id.removeprefix("agent_skill:"),
            source_uri,
            json.dumps(attrs),
        ),
    )


@pytest.mark.offline
def test_worker_message_includes_score_zero_candidate() -> None:
    candidates = [
        {
            "slug": "matched",
            "description": "Matched",
            "trigger_short": "match",
            "score": 2.0,
        },
        {
            "slug": "unmatched",
            "description": "Unmatched",
            "trigger_short": "other",
            "score": 0,
        },
    ]
    msg = build_worker_message(
        loaded=[],
        conversation_context="match context",
        agent="claude-cursor",
        limit=8,
        all_candidates=candidates,
    )
    assert "light-bounded" in msg
    assert '"stage_a_score": 0' in msg


@pytest.mark.offline
def test_extended_candidates_include_score_zero() -> None:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:matched",
        source_uri="agent-skills/matched.md",
        trigger_match_terms=["match"],
    )
    _insert(
        conn,
        "agent_skill:unmatched",
        source_uri="agent-skills/unmatched.md",
        trigger_match_terms=["other"],
    )
    conn.commit()
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-cursor",
            loaded=[],
            conversation_context="match context",
            limit=8,
            return_all_candidates=True,
        )
    extended = result.get("stage_a_extended_candidates") or []
    scores = {item["slug"]: item["score"] for item in extended}
    assert scores.get("matched", 0) > 0
    assert scores.get("unmatched") == 0


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_contract_is_light_bounded() -> None:
    body = SkillSuggestDispatchRequest(agent="claude-cursor", loaded=[])
    with (
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._fetch_extended_candidates",
            new_callable=AsyncMock,
            return_value=([], [], []),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.dispatch_cursor_sdk_generate",
            new_callable=AsyncMock,
            side_effect=FrontierEndpointError(
                request_id="req-ac14",
                field="contract",
                reason="rejected",
            ),
        ) as dispatch,
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.run_fallback",
            new_callable=AsyncMock,
            return_value={
                "agent": "claude-cursor",
                "suggestions": [],
                "count": 0,
                "omitted": [],
                "degraded_skills": [],
                "loaded_echo": [],
                "seat_preloaded": [],
                "ranker_status": "pending",
                "degraded": False,
                "route": "fallback",
            },
        ),
        patch("systems.frontier_consult.skill_suggest_dispatch._publish_event"),
    ):
        await dispatch_skill_suggest(request_id="req-ac14-a", body=body)
    assert dispatch.await_args.kwargs["contract"] == "light-bounded"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_timeout_yields_pending() -> None:
    body = SkillSuggestDispatchRequest(agent="claude-cursor", loaded=[])
    fallback_payload = {
        "agent": "claude-cursor",
        "suggestions": [],
        "count": 0,
        "omitted": [],
        "degraded_skills": [],
        "loaded_echo": [],
        "seat_preloaded": [],
        "ranker_status": "pending",
        "degraded": False,
        "route": "fallback",
    }
    with (
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._fetch_extended_candidates",
            new_callable=AsyncMock,
            return_value=([], [], []),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.dispatch_cursor_sdk_generate",
            new_callable=AsyncMock,
            return_value={"execution_id": "exec-t", "thread_id": "2999"},
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_ack",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_completion",
            new_callable=AsyncMock,
            return_value=WorkerWaitOutcome(kind="idle_timeout"),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.fetch_worker_closeout_body",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.run_fallback",
            new_callable=AsyncMock,
            return_value=fallback_payload,
        ),
        patch("systems.frontier_consult.skill_suggest_dispatch._publish_event"),
    ):
        result = await dispatch_skill_suggest(request_id="req-ac14-d", body=body)
    assert result["ranker_status"] == "pending"
    assert result["degraded"] is True
    assert result["degraded_reason"] == "worker_idle_timeout"
