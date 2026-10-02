"""Accepted-scope ok+0 must carry empty_reason (a:37162)."""

from __future__ import annotations

import sys
from pathlib import Path

_MCP = Path(__file__).resolve().parents[1]
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

from tools import _rag_search_exec  # noqa: E402

_SENTINEL = "No relevant documents found in the knowledge base."


def _zero_response(*, empty_reason: str | None = None) -> dict:
    retrieval = {
        "resolved_scope": "llm_prompting",
        "chunks_found": 0,
        "scope_rejected": False,
        "scope_source": "user_override",
        "scope_key": "llm_prompting",
        "weak_match": None,
        "rerank_status": "disabled",
        "weak_match_basis": "none",
    }
    if empty_reason is not None:
        retrieval["empty_reason"] = empty_reason
    return {
        "choices": [{"message": {"content": _SENTINEL}}],
        "pipeline": {"retrieval": retrieval},
    }


def test_ok_zero_without_empty_reason_gets_unreported(monkeypatch) -> None:
    """Pre-fix hazard: ok+sentinel+0 with no distinguisher is unreadable."""

    monkeypatch.setattr(
        _rag_search_exec,
        "pipeline_call",
        lambda *args, **kwargs: _zero_response(),
    )
    env = _rag_search_exec.run_rag_search(
        "commissioning cursor agent investigate fix bug prompt structure",
        scope="llm_prompting",
        prefixes=None,
        pipeline_options={},
        unscoped=False,
    )
    assert env["status"] == "ok"
    assert env["context"] == _SENTINEL
    assert env["retrieval"]["chunks_found"] == 0
    assert env["retrieval"]["empty_reason"] == "unreported"
    assert "retryable" not in env


def test_chunks_found_none_does_not_synthesize_empty_reason(monkeypatch) -> None:
    body = _zero_response()
    body["pipeline"]["retrieval"]["chunks_found"] = None

    monkeypatch.setattr(
        _rag_search_exec,
        "pipeline_call",
        lambda *args, **kwargs: body,
    )
    env = _rag_search_exec.run_rag_search(
        "missing count",
        scope="llm_prompting",
        prefixes=None,
        pipeline_options={},
        unscoped=False,
    )
    assert env["status"] == "ok"
    assert "empty_reason" not in env["retrieval"]


def test_pipeline_empty_reason_is_preserved(monkeypatch) -> None:
    monkeypatch.setattr(
        _rag_search_exec,
        "pipeline_call",
        lambda *args, **kwargs: _zero_response(empty_reason="junk_filtered"),
    )
    env = _rag_search_exec.run_rag_search(
        "noise-only query",
        scope="llm_prompting",
        prefixes=None,
        pipeline_options={},
        unscoped=False,
    )
    assert env["status"] == "ok"
    assert env["retrieval"]["empty_reason"] == "junk_filtered"


def test_scope_rejection_does_not_synthesize_empty_reason(monkeypatch) -> None:
    body = _zero_response()
    body["pipeline"]["retrieval"]["scope_rejected"] = True
    body["pipeline"]["retrieval"]["scope_rejection_reason"] = "invalid_scope_override"

    monkeypatch.setattr(
        _rag_search_exec,
        "pipeline_call",
        lambda *args, **kwargs: body,
    )
    env = _rag_search_exec.run_rag_search(
        "bad scope",
        scope="not-a-scope",
        prefixes=None,
        pipeline_options={},
        unscoped=False,
    )
    assert env["status"] == "ok"
    assert env["retrieval"]["scope_rejection_reason"] == "invalid_scope_override"
    assert "empty_reason" not in env["retrieval"]
