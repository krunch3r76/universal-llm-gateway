"""Rerank tests for skill_suggest Stage-B (spec §8 tests 17–22)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from cortex_store.routes._skill_suggest import run_stage_a
from cortex_store.skill_suggest_rank import apply_rerank, reset_circuit_for_tests

_FIXTURES = Path(__file__).resolve().parent / "tests" / "fixtures"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _stage_a_result() -> dict:
    return {
        "suggestions": [
            {
                "id": "agent_skill:consult-routing",
                "slug": "consult-routing",
                "uri": "cortex://agent-skills/consult-routing.md",
                "score": 2.0,
                "trigger_match": ["consult"],
                "reason": "matches: consult",
                "reason_source": "deterministic",
            },
            {
                "id": "agent_skill:session-close",
                "slug": "session-close",
                "uri": "cortex://agent-skills/session-close.md",
                "score": 1.0,
                "trigger_match": ["session"],
                "reason": "matches: session",
                "reason_source": "deterministic",
            },
        ],
        "loaded_echo": [],
        "omitted": [],
        "ranker_status": "disabled",
        "degraded": False,
        "agent": "claude-web",
        "count": 2,
    }


def _candidates() -> list[dict]:
    return [
        {
            "slug": "consult-routing",
            "trigger_short": "consult routing",
            "trigger_match_terms": ["consult"],
            "skill_category": "workflow",
        },
        {
            "slug": "session-close",
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
                "choices": [{"message": {"content": _fixture("skill_suggest_rank_valid.json")}}],
                "execution_id": "exec-1",
            }
        )

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, json=None: _post(url, json))
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


def test_rerank_drops_hallucinated_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_circuit_for_tests()

    def _post(url: str, json: dict) -> _StubResponse:  # noqa: A002
        del url, json
        return _StubResponse(
            {
                "choices": [
                    {"message": {"content": _fixture("skill_suggest_rank_hallucinated.json")}}
                ]
            }
        )

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, json=None: _post(url, json))
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

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, json=None: _post(url, json))
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

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, json=None: _post(url, json))
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
    assert result["suggestions"][0]["reason_source"] == "deterministic"


def test_rerank_timeout_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_circuit_for_tests()

    def _post(url: str, json: dict) -> _StubResponse:  # noqa: A002
        del url, json
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, json=None: _post(url, json))
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


def test_rerank_flag_off_skips_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILL_SUGGEST_RERANK_ENABLED", "false")
    called = {"n": 0}

    def _post(url: str, json: dict) -> _StubResponse:  # noqa: A002
        called["n"] += 1
        del url, json
        return _StubResponse({"choices": []})

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, json=None: _post(url, json))
    stage_a = run_stage_a(
        agent="claude-web",
        loaded=[],
        conversation_context="",
        limit=8,
    )
    assert stage_a["ranker_status"] == "skipped_no_context"
    assert called["n"] == 0
