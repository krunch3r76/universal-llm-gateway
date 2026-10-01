"""RAG retrieval transport failures must not masquerade as an empty corpus."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

_MCP = Path(__file__).resolve().parents[1]
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

from tools import _rag_inflight, _rag_search_exec  # noqa: E402

_SENTINEL = "No relevant documents found in the knowledge base."

_SCOPE_CATALOG_PATH = (
    Path(__file__).resolve().parents[3]
    / "pipelines/rag/rag_context_v1/handlers/scope_catalog.py"
)
_scope_spec = importlib.util.spec_from_file_location(
    "scope_catalog_retrieval_unavailable_test", _SCOPE_CATALOG_PATH
)
assert _scope_spec and _scope_spec.loader
_scope_mod = importlib.util.module_from_spec(_scope_spec)
sys.modules[_scope_spec.name] = _scope_mod
_scope_spec.loader.exec_module(_scope_mod)


def _retrieval_down_response() -> dict:
    return {
        "choices": [{"message": {"content": _SENTINEL}}],
        "pipeline": {
            "retrieval": {
                "resolved_scope": "llm_prompting",
                "chunks_found": 0,
                "scope_rejected": False,
                "scope_source": "user_override",
                "retrieval_rejection_reason": "retrieval_unavailable",
            }
        },
    }


def test_connect_error_all_queries_yields_retryable_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hermetic falsifier: rag-down path is error + retryable, not ok + sentinel."""

    def fake_pipeline_call(*args, **kwargs):
        return _retrieval_down_response()

    monkeypatch.setattr(_rag_search_exec, "pipeline_call", fake_pipeline_call)
    env = _rag_search_exec.run_rag_search(
        "rag service down probe",
        scope="llm_prompting",
        prefixes=None,
        pipeline_options={},
        unscoped=False,
    )
    assert env["status"] == "error"
    assert env["retryable"] is True
    assert "context" not in env
    assert env["retrieval"]["retrieval_rejection_reason"] == "retrieval_unavailable"


def test_stale_catalog_still_validates_scope_under_search_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scope was validated from last-known-good catalog before search failed."""

    class _Boom:
        def raise_for_status(self) -> None:
            raise RuntimeError("timeout")

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, path: str) -> _Boom:
            return _Boom()

    client = _Client()
    monkeypatch.setattr(_scope_mod, "make_async_client", lambda *a, **k: client)
    monkeypatch.setattr(_scope_mod.time, "monotonic", lambda: 2_000.0)
    _scope_mod._cache_scopes = {"llm_prompting", "code_retrieval"}
    _scope_mod._cache_prefixes = {}
    _scope_mod._cache_ts = 2_000.0 - 120.0
    _scope_mod._cache_last_attempt_ts = 0.0

    async def _run() -> set[str] | None:
        return await _scope_mod.fetch_valid_scopes("http://rag")

    try:
        scopes = asyncio.run(_run())
    finally:
        _scope_mod._cache_scopes = None
        _scope_mod._cache_prefixes = None
        _scope_mod._cache_ts = 0.0
        _scope_mod._cache_last_attempt_ts = 0.0

    assert scopes == {"llm_prompting", "code_retrieval"}

    def fake_pipeline_call(*args, **kwargs):
        body = _retrieval_down_response()
        assert body["pipeline"]["retrieval"]["scope_rejected"] is False
        return body

    monkeypatch.setattr(_rag_search_exec, "pipeline_call", fake_pipeline_call)
    env = _rag_search_exec.run_rag_search(
        "stale catalog + search down",
        scope="llm_prompting",
        prefixes=None,
        pipeline_options={},
        unscoped=False,
    )
    assert env["status"] == "error"
    assert env["retryable"] is True
    assert env["retrieval"]["resolved_scope"] == "llm_prompting"


def _clear_registry() -> None:
    with _rag_inflight._lock:
        _rag_inflight._by_key.clear()
        _rag_inflight._by_id.clear()


def test_retrieval_unavailable_is_not_a_ten_minute_cache_hit() -> None:
    _clear_registry()
    calls = {"n": 0}

    def start() -> dict:
        calls["n"] += 1
        return {
            "status": "error",
            "retryable": True,
            "retrieval": {"retrieval_rejection_reason": "retrieval_unavailable"},
        }

    try:
        first = _rag_inflight.admit_search("retrieval-down-nonce", start)
        assert first.wait()["status"] == "error"
        second = _rag_inflight.admit_search("retrieval-down-nonce", start)
        assert second.cache_hit is False
        assert second.wait()["status"] == "error"
        assert calls["n"] == 2
    finally:
        _clear_registry()

