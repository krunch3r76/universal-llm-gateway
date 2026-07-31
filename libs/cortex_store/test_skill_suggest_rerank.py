"""Rerank tests for skill_suggest Stage-B (spec §8 tests 17–22)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from cortex_store.routes._skill_suggest import run_stage_a
from cortex_store.routes.skills import post_skill_suggest
from cortex_store.skill_suggest_rank import apply_rerank, reset_circuit_for_tests

_FIXTURES = Path(__file__).resolve().parent / "tests" / "fixtures"

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
    applicable_agents: list[str] | None = None,
) -> None:
    attrs: dict[str, object] = {}
    if applicable_agents is not None:
        attrs["applicable_agents"] = applicable_agents
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


class _FakeRequest:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _stage_a_result() -> dict:
    return {
        "suggestions": [
            {
                "id": "agent_skill:consult-routing",
                "slug": "consult-routing",
                "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/consult-routing/SKILL.md",
                "digest": None,
                "score": 2.0,
                "description": "",
                "reason": "Skill consult-routing",
                "reason_source": "deterministic",
            },
            {
                "id": "agent_skill:session-close",
                "slug": "session-close",
                "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/session-close/SKILL.md",
                "digest": None,
                "score": 1.0,
                "description": "Close sessions with provenance discipline.",
                "reason": "Close sessions with provenance discipline.",
                "reason_source": "deterministic",
            },
        ],
        "loaded_echo": [],
        "omitted": [],
        "ranker_status": "pending",
        "degraded": False,
        "agent": "claude-web",
        "count": 2,
    }


def _candidates() -> list[dict]:
    return [
        {
            "slug": "consult-routing",
            "description": "",
            "trigger_short": "consult routing",
            "trigger_match_terms": ["consult"],
            "skill_category": "workflow",
        },
        {
            "slug": "session-close",
            "description": "Close sessions with provenance discipline.",
            "trigger_short": "session close",
            "trigger_match_terms": ["session"],
            "skill_category": "workflow",
        },
    ]


class _StubResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_rerank_valid_reorders(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_circuit_for_tests()

    def _post(url: str, json: dict) -> _StubResponse:  # noqa: A002
        del url, json
        return _StubResponse(
            {
                "choices": [
                    {"message": {"content": _fixture("skill_suggest_rank_valid.json")}}
                ],
                "execution_id": "exec-1",
            }
        )

    monkeypatch.setattr(
        httpx.Client, "post", lambda self, url, json=None: _post(url, json)
    )
    result, status, degraded_reason, exec_id = apply_rerank(
        stage_a_result=_stage_a_result(),
        stage_a_candidates=_candidates(),
        conversation_context="consult session",
        loaded=[],
        limit=8,
    )
    assert status == "ok"
    assert degraded_reason is None
    assert exec_id == "exec-1"
    assert result["suggestions"][0]["slug"] == "consult-routing"
    assert result["suggestions"][0]["reason_source"] == "model"
    assert result["suggestions"][0]["description"]


def test_rerank_describe_only_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_circuit_for_tests()

    def _post(url: str, json: dict) -> _StubResponse:  # noqa: A002
        del url, json
        return _StubResponse(
            {
                "choices": [
                    {"message": {"content": _fixture("skill_suggest_rank_valid.json")}}
                ],
                "execution_id": "exec-describe",
            }
        )

    monkeypatch.setattr(
        httpx.Client, "post", lambda self, url, json=None: _post(url, json)
    )
    stage_a = _stage_a_result()
    result, status, _, _ = apply_rerank(
        stage_a_result=stage_a,
        stage_a_candidates=_candidates(),
        conversation_context="consult session",
        loaded=[],
        limit=8,
        describe_only=True,
    )
    assert status == "ok"
    assert [s["slug"] for s in result["suggestions"]] == [
        s["slug"] for s in stage_a["suggestions"]
    ]
    assert result["suggestions"][0]["description"]


def test_rerank_drops_hallucinated_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_circuit_for_tests()

    def _post(url: str, json: dict) -> _StubResponse:  # noqa: A002
        del url, json
        return _StubResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": _fixture("skill_suggest_rank_hallucinated.json")
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(
        httpx.Client, "post", lambda self, url, json=None: _post(url, json)
    )
    result, status, _, _ = apply_rerank(
        stage_a_result=_stage_a_result(),
        stage_a_candidates=_candidates(),
        conversation_context="consult",
        loaded=[],
        limit=8,
    )
    assert status == "ok"
    slugs = [s["slug"] for s in result["suggestions"]]
    assert "hallucinated-skill" not in slugs


def test_rerank_drops_loaded_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_circuit_for_tests()

    def _post(url: str, json: dict) -> _StubResponse:  # noqa: A002
        del url, json
        return _StubResponse(
            {
                "choices": [
                    {"message": {"content": _fixture("skill_suggest_rank_loaded.json")}}
                ]
            }
        )

    monkeypatch.setattr(
        httpx.Client, "post", lambda self, url, json=None: _post(url, json)
    )
    result, status, _, _ = apply_rerank(
        stage_a_result=_stage_a_result(),
        stage_a_candidates=_candidates(),
        conversation_context="consult",
        loaded=["fs"],
        limit=8,
    )
    assert status == "ok"
    assert all(s["slug"] != "fs" for s in result["suggestions"])


@pytest.mark.parametrize(
    "fixture_name",
    ["skill_suggest_rank_malformed.json", "skill_suggest_rank_empty.json"],
)
def test_rerank_invalid_json_fallback(
    monkeypatch: pytest.MonkeyPatch, fixture_name: str
) -> None:
    reset_circuit_for_tests()

    def _post(url: str, json: dict) -> _StubResponse:  # noqa: A002
        del url, json
        return _StubResponse(
            {"choices": [{"message": {"content": _fixture(fixture_name)}}]}
        )

    monkeypatch.setattr(
        httpx.Client, "post", lambda self, url, json=None: _post(url, json)
    )
    result, status, degraded_reason, _ = apply_rerank(
        stage_a_result=_stage_a_result(),
        stage_a_candidates=_candidates(),
        conversation_context="consult",
        loaded=[],
        limit=8,
    )
    assert status == "invalid_output"
    assert result["degraded"] is True
    assert degraded_reason == "invalid_output"
    assert result["suggestions"] == []
    assert result["count"] == 0
    assert result["warnings"][0]["code"] == "ranker_degraded"


def test_rerank_timeout_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_circuit_for_tests()

    def _post(url: str, json: dict) -> _StubResponse:  # noqa: A002
        del url, json
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(
        httpx.Client, "post", lambda self, url, json=None: _post(url, json)
    )
    result, status, degraded_reason, _ = apply_rerank(
        stage_a_result=_stage_a_result(),
        stage_a_candidates=_candidates(),
        conversation_context="consult",
        loaded=[],
        limit=8,
    )
    assert status == "timeout"
    assert result["degraded"] is True
    assert degraded_reason == "timeout"
    assert result["suggestions"] == []
    assert result["count"] == 0
    assert result["warnings"][0]["code"] == "ranker_degraded"


def test_rerank_flag_off_skips_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILL_SUGGEST_RERANK_ENABLED", "false")
    called = {"n": 0}

    def _post(url: str, json: dict) -> _StubResponse:  # noqa: A002
        called["n"] += 1
        del url, json
        return _StubResponse({"choices": []})

    monkeypatch.setattr(
        httpx.Client, "post", lambda self, url, json=None: _post(url, json)
    )
    stage_a = run_stage_a(
        agent="claude-web",
        loaded=[],
        conversation_context="",
        limit=8,
    )
    assert stage_a["ranker_status"] == "skipped_no_context"
    assert called["n"] == 0


@pytest.fixture()
def suggest_conn() -> sqlite3.Connection:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:consult-routing",
        source_uri="workspaces://universal-llm-gateway/.cursor/skills/consult-routing/SKILL.md",
        trigger_match_terms=["consult", "handoff"],
        applicable_agents=["claude-web"],
    )
    conn.commit()
    return conn


@pytest.mark.parametrize(
    ("ranker_status", "degraded_reason"),
    [
        ("timeout", "timeout"),
        ("invalid_output", "invalid_output"),
        ("error", "circuit_open"),
        ("error", "concurrency_cap"),
        ("error", "error"),
    ],
)
@pytest.mark.asyncio
async def test_route_envelope_honesty_on_rerank_failure(
    monkeypatch: pytest.MonkeyPatch,
    suggest_conn: sqlite3.Connection,
    ranker_status: str,
    degraded_reason: str,
) -> None:
    """Envelope ranker_status/degraded_reason must match telemetry locals, not stale disabled."""
    telemetry: dict[str, object] = {}

    def _fake_apply_rerank(**_kwargs):
        result = _stage_a_result()
        result["degraded"] = True
        result["suggestions"] = []
        result["count"] = 0
        result["warnings"] = [
            {
                "code": "ranker_degraded",
                "reason": degraded_reason,
                "message": "no suggestions returned (deterministic fallback disabled); callers may retry.",
            }
        ]
        return result, ranker_status, degraded_reason, None

    def _capture_completed(**kwargs):
        telemetry.update(kwargs)

    def _capture_degraded(**kwargs):
        telemetry.update(kwargs)

    monkeypatch.setenv("SKILL_SUGGEST_RERANK_ENABLED", "true")
    monkeypatch.setattr(
        "cortex_store.routes.skills.apply_rerank",
        _fake_apply_rerank,
    )
    monkeypatch.setattr(
        "cortex_store.routes.skills.cortex_skill_suggest_completed",
        _capture_completed,
    )
    monkeypatch.setattr(
        "cortex_store.routes.skills.cortex_skill_suggest_degraded",
        _capture_degraded,
    )

    with patch(
        "cortex_store.routes._skill_suggest.cortex_conn", return_value=suggest_conn
    ):
        with patch("cortex_store.routes.skills.cortex_conn", return_value=suggest_conn):
            with patch(
                "cortex_store.routes._skill_suggest._seat_preloaded_norm_slugs",
                return_value=frozenset(),
            ):
                body = await post_skill_suggest(
                    _FakeRequest(
                        {
                            "agent": "claude-web",
                            "loaded": [],
                            "conversation_context": "consult handoff routing",
                            "rerank": True,
                        }
                    )
                )

    assert body["ranker_status"] == ranker_status
    assert body["suggestions"] == []
    assert body["count"] == 0
    assert body["degraded"] is True
    assert body["warnings"][0]["code"] == "ranker_degraded"
    assert body["degraded_reason"] == degraded_reason
    assert telemetry["ranker_status"] == ranker_status
    assert telemetry["degraded_reason"] == degraded_reason
