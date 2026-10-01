"""Catalog outages are retryable errors, not a ten-minute empty corpus."""

from __future__ import annotations

import sys
from pathlib import Path

_MCP = Path(__file__).resolve().parents[1]
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

from tools import _rag_inflight, _rag_search_exec  # noqa: E402

_SENTINEL = "No relevant documents found in the knowledge base."


def _catalog_response() -> dict:
    return {
        "choices": [{"message": {"content": _SENTINEL}}],
        "pipeline": {
            "retrieval": {
                "resolved_scope": "code_retrieval",
                "chunks_found": 0,
                "scope_rejected": True,
                "scope_rejection_reason": "scope_catalog_unavailable",
                "scope_source": "user_override",
            }
        },
    }


def _ok_response() -> dict:
    return {
        "choices": [{"message": {"content": "[Source: paper.pdf]\n\nbody"}}],
        "pipeline": {
            "retrieval": {
                "resolved_scope": "code_retrieval",
                "chunks_found": 1,
                "scope_rejected": False,
                "scope_source": "user_override",
                "weak_match": False,
                "rerank_status": "ok",
                "weak_match_basis": "cross_encoder",
            }
        },
    }


def test_catalog_outage_retries_once_then_returns_error(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_pipeline_call(*args, **kwargs):
        calls["n"] += 1
        return _catalog_response()

    monkeypatch.setattr(_rag_search_exec, "pipeline_call", fake_pipeline_call)
    env = _rag_search_exec.run_rag_search(
        "catalog outage probe",
        scope="code_retrieval",
        prefixes=None,
        pipeline_options={},
        unscoped=False,
    )
    assert calls["n"] == 2
    assert env["status"] == "error"
    assert env["retryable"] is True
    assert "context" not in env
    assert env["retrieval"]["scope_rejection_reason"] == "scope_catalog_unavailable"


def test_catalog_outage_succeeds_on_the_retry(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_pipeline_call(*args, **kwargs):
        calls["n"] += 1
        return _catalog_response() if calls["n"] == 1 else _ok_response()

    monkeypatch.setattr(_rag_search_exec, "pipeline_call", fake_pipeline_call)
    env = _rag_search_exec.run_rag_search(
        "catalog recovered",
        scope="code_retrieval",
        prefixes=None,
        pipeline_options={},
        unscoped=False,
    )
    assert calls["n"] == 2
    assert env["status"] == "ok"
    assert env["context"].startswith("[Source:")
    assert env["retrieval"]["weak_match_basis"] == "cross_encoder"
    assert env["retrieval"]["rerank_status"] == "ok"


def test_invalid_scope_is_not_retried_or_rewritten(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_pipeline_call(*args, **kwargs):
        calls["n"] += 1
        body = _catalog_response()
        body["pipeline"]["retrieval"]["scope_rejection_reason"] = "invalid_scope_override"
        return body

    monkeypatch.setattr(_rag_search_exec, "pipeline_call", fake_pipeline_call)
    env = _rag_search_exec.run_rag_search(
        "bad scope",
        scope="not-a-scope",
        prefixes=None,
        pipeline_options={},
        unscoped=False,
    )
    assert calls["n"] == 1
    assert env["status"] == "ok"
    assert env["context"] == _SENTINEL
    assert env["retrieval"]["scope_rejection_reason"] == "invalid_scope_override"


def _clear_registry() -> None:
    with _rag_inflight._lock:
        _rag_inflight._by_key.clear()
        _rag_inflight._by_id.clear()


def test_retryable_catalog_envelope_is_not_served_as_a_cache_hit() -> None:
    _clear_registry()
    starts = {"n": 0}

    def start() -> dict:
        starts["n"] += 1
        return {
            "status": "error",
            "retryable": True,
            "retrieval": {"scope_rejection_reason": "scope_catalog_unavailable"},
        }

    try:
        first = _rag_inflight.admit_search("catalog-key", start)
        assert first.wait()["status"] == "error"
        second = _rag_inflight.admit_search("catalog-key", start)
        assert second.cache_hit is False
        assert second.wait()["status"] == "error"
        assert starts["n"] == 2
    finally:
        _clear_registry()


def test_identical_in_flight_searches_still_share_one_call() -> None:
    import threading

    _clear_registry()
    started = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def start() -> dict:
        calls["n"] += 1
        started.set()
        assert release.wait(2)
        return {"status": "ok", "context": "shared"}

    try:
        first = _rag_inflight.admit_search("dedup-nonce", start)
        assert started.wait(2)
        second = _rag_inflight.admit_search("dedup-nonce", start)
        release.set()
        assert first.wait()["context"] == "shared"
        assert second.wait()["context"] == "shared"
        assert second.attached is True
        assert calls["n"] == 1
    finally:
        _clear_registry()


def test_successful_envelope_still_cache_hits() -> None:
    _clear_registry()
    starts = {"n": 0}

    def start() -> dict:
        starts["n"] += 1
        return {"status": "ok", "context": "body"}

    try:
        first = _rag_inflight.admit_search("ok-key", start)
        assert first.wait()["status"] == "ok"
        second = _rag_inflight.admit_search("ok-key", start)
        assert second.cache_hit is True
        assert second.wait()["context"] == "body"
        assert starts["n"] == 1
    finally:
        _clear_registry()
