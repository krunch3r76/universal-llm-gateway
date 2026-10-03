"""RAG retrieval transport failures must not masquerade as an empty corpus."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_MCP = Path(__file__).resolve().parents[1]
_REPO = Path(__file__).resolve().parents[3]
_HANDLERS = _REPO / "pipelines/rag_search/v1/handlers"
_RAG_V1 = _HANDLERS.parent

if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

from tools import _rag_inflight, _rag_search_exec  # noqa: E402
from tools._rag_inflight import (  # noqa: E402
    UnknownSearchError,
    admit_search,
    attach_search,
    search_key,
)

_SCOPE_CATALOG_PATH = _HANDLERS / "scope_catalog.py"
_scope_spec = importlib.util.spec_from_file_location(
    "scope_catalog_retrieval_unavailable_test", _SCOPE_CATALOG_PATH
)
assert _scope_spec and _scope_spec.loader
_scope_mod = importlib.util.module_from_spec(_scope_spec)
sys.modules[_scope_spec.name] = _scope_mod
_scope_spec.loader.exec_module(_scope_mod)


class _InflightClock:
    """Controllable monotonic clock for ``_rag_inflight`` TTL assertions only."""

    def __init__(self, start: float = 10_000.0) -> None:
        self.t = start

    def monotonic(self) -> float:
        return self.t

    def set(self, value: float) -> None:
        self.t = value


def _load_handler_module(module_name: str, filename: str) -> Any:
    path = _HANDLERS / filename
    spec = importlib.util.spec_from_file_location(
        module_name,
        path,
        submodule_search_locations=[str(_HANDLERS), str(_RAG_V1)],
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _clear_registry() -> None:
    with _rag_inflight._lock:
        _rag_inflight._by_key.clear()
        _rag_inflight._by_id.clear()


def _reset_scope_catalog_cache() -> None:
    _scope_mod._cache_scopes = None
    _scope_mod._cache_prefixes = None
    _scope_mod._cache_ts = 0.0
    _scope_mod._cache_last_attempt_ts = 0.0


def _install_scope_catalog(
    monkeypatch: pytest.MonkeyPatch, *, stale: bool
) -> None:
    """Fresh catalog: /scopes succeeds. Stale: last-known-good inside TTL, refresh fails."""

    class _OkScopes:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"scopes": {"llm_prompting": {"prefixes": []}}}

    class _FailScopes:
        def raise_for_status(self) -> None:
            raise RuntimeError("catalog refresh down")

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, path: str) -> _OkScopes | _FailScopes:
            if stale:
                return _FailScopes()
            return _OkScopes()

    client = _Client()
    monkeypatch.setattr(_scope_mod, "make_async_client", lambda *a, **k: client)
    fixed_now = 2_000.0
    monkeypatch.setattr(
        _scope_mod,
        "time",
        SimpleNamespace(monotonic=lambda: fixed_now),
    )
    if stale:
        _scope_mod._cache_scopes = {"llm_prompting"}
        _scope_mod._cache_prefixes = {}
        _scope_mod._cache_ts = fixed_now - 120.0
        _scope_mod._cache_last_attempt_ts = 0.0
    else:
        _reset_scope_catalog_cache()


def _build_stargate_body(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stale_catalog: bool,
    query: str,
) -> dict[str, Any]:
    """catalog → retrieve step JSON → Stargate-shaped completion (production metadata path)."""

    from systems.pipeline.core.executor.output_resolution import (  # noqa: WPS433
        extract_retrieval_metadata,
    )
    from systems.pipeline.core.handlers.protocol import StepOutput  # noqa: WPS433
    from systems.pipeline.core.schemas import StepConfig  # noqa: WPS433

    rq = _load_handler_module(
        f"rag_query_retrieve_ut_{stale_catalog}", "rag_query_retrieve.py"
    )
    ds = _load_handler_module(
        f"rag_direct_scope_ut_{stale_catalog}", "direct_scope.py"
    )

    _install_scope_catalog(monkeypatch, stale=stale_catalog)
    rq.fetch_valid_scopes = _scope_mod.fetch_valid_scopes
    rq.fetch_scope_prefixes = _scope_mod.fetch_scope_prefixes

    async def _connect_error(*_args: object, **_kwargs: object) -> None:
        raise httpx.ConnectError("all search queries down")

    monkeypatch.setattr(rq, "_execute_single_query", _connect_error)

    class _EmbedResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"embeddings": [[]]}

    class _RagClient:
        async def __aenter__(self) -> _RagClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, path: str, json: dict[str, Any] | None = None) -> _EmbedResponse:
            if path.endswith("embed_batch") or "/embed_batch" in path:
                return _EmbedResponse()
            raise AssertionError(f"unexpected RAG POST {path}")

    monkeypatch.setattr(rq, "make_async_client", lambda *a, **k: _RagClient())
    monkeypatch.setattr(rq, "resolve_rag_base_url", lambda: "http://rag.test")

    pipeline = MagicMock()
    pipeline.id = "rag-search"
    pipeline.domain = "rag"
    pipeline.options.to_context_dict.return_value = {
        "rewrite_enabled": False,
        "hyde_enabled": False,
        "rerank_enabled": False,
        "pool_b_enabled": False,
        "source_only_retrieval": True,
    }

    context = MagicMock()
    context.pipeline = pipeline
    context.source_text = query
    context.runtime_options = {"scope_override": "llm_prompting"}
    context.execution_id = "hermetic-exec"
    context.recorder = None
    context.outputs = {}
    context._proxy = MagicMock()
    context._proxy.event_bus.publish_nowait = AsyncMock()
    context.get_output = lambda step_id: context.outputs.get(step_id)
    type(context).options = property(
        lambda self: {**pipeline.options.to_context_dict(), **self.runtime_options}
    )

    direct_step = StepConfig(id="direct_scope", type="rag_direct_scope_v1", depends_on=[])
    direct_out = asyncio.run(ds.DirectScopeHandler().execute(direct_step, context))
    context.outputs["direct_scope"] = direct_out
    context.outputs["generate_hyde"] = StepOutput(raw="", json={"_skipped": True})

    retrieve_step = StepConfig(
        id="retrieve_assemble",
        type="rag_multi_retrieve_v1",
        depends_on=[],
        handler_inputs={
            "scope_result": "direct_scope.json",
            "rewrite_result": "direct_scope.json",
            "hyde_result": "generate_hyde.json",
        },
        endpoint="/search",
    )

    retrieve_out = asyncio.run(
        rq.RagMultiRetrieveHandler().execute(retrieve_step, context)
    )
    context.outputs["retrieve_assemble"] = retrieve_out

    steps = [direct_step, retrieve_step]
    retrieval = extract_retrieval_metadata(context, steps)
    assert retrieval is not None
    assert retrieval.get("retrieval_rejection_reason") == "retrieval_unavailable"

    return {
        "choices": [{"message": {"content": retrieve_out.raw}}],
        "pipeline": {"retrieval": retrieval},
    }


def _rag_search_production(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stale_catalog: bool,
    query: str,
    pipeline_options: dict[str, Any],
    pipeline_calls: dict[str, int],
) -> tuple[dict[str, Any], str, str]:
    """MCP rag_search path: inflight registry → run_rag_search → stamped envelope."""

    def _pipeline_call(*_args: object, **_kwargs: object) -> dict[str, Any]:
        pipeline_calls["n"] += 1
        return _build_stargate_body(
            monkeypatch, stale_catalog=stale_catalog, query=query
        )

    monkeypatch.setattr(_rag_search_exec, "pipeline_call", _pipeline_call)

    key = search_key(query, pipeline_options)

    def _start() -> dict[str, Any]:
        return _rag_search_exec.run_rag_search(
            query,
            scope="llm_prompting",
            prefixes=None,
            pipeline_options=dict(pipeline_options),
            unscoped=False,
        )

    ticket = admit_search(key, _start)
    env = ticket.stamp(ticket.wait())
    return env, ticket.search_id, key


@pytest.mark.parametrize("stale_catalog", [False, True])
def test_connect_error_all_queries_yields_retryable_envelope(
    monkeypatch: pytest.MonkeyPatch,
    stale_catalog: bool,
) -> None:
    """Every query raises ConnectError; production path yields retryable error + fresh re-issue."""

    _clear_registry()
    pipeline_calls = {"n": 0}
    query = (
        "stale catalog retrieval down" if stale_catalog else "fresh catalog retrieval down"
    )
    pipeline_options = {
        "scope_override": "llm_prompting",
        "include_retrieval_metadata": True,
    }

    try:
        env, search_id, key = _rag_search_production(
            monkeypatch,
            stale_catalog=stale_catalog,
            query=query,
            pipeline_options=pipeline_options,
            pipeline_calls=pipeline_calls,
        )
        assert pipeline_calls["n"] == 1
        assert env["status"] == "error"
        assert env["retryable"] is True
        assert "context" not in env
        assert env["retrieval"]["retrieval_rejection_reason"] == "retrieval_unavailable"
        assert env["retrieval"]["scope_rejected"] is False
        assert "cache_hit" not in env

        poll = attach_search(search_id)
        assert poll.cache_hit is False
        polled = poll.stamp(poll.wait())
        assert polled["status"] == "error"
        assert polled.get("cache_hit") is not True
        assert pipeline_calls["n"] == 1

        retry = admit_search(
            key,
            lambda: _rag_search_exec.run_rag_search(
                query,
                scope="llm_prompting",
                prefixes=None,
                pipeline_options=dict(pipeline_options),
                unscoped=False,
            ),
        )
        assert retry.cache_hit is False
        assert retry.wait()["status"] == "error"
        assert retry.search_id != search_id
        assert pipeline_calls["n"] == 2
    finally:
        _clear_registry()
        _reset_scope_catalog_cache()


def test_inflight_failure_ttl_vs_success_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure TTL (30s) evicts retryable errors; success cache keeps 600s."""

    clock = _InflightClock(10_000.0)
    monkeypatch.setattr(_rag_inflight, "time", clock)
    _clear_registry()

    def _fail_start() -> dict[str, Any]:
        return {
            "status": "error",
            "retryable": True,
            "retrieval": {"retrieval_rejection_reason": "retrieval_unavailable"},
        }

    try:
        fail_ticket = admit_search("failure-ttl-key", _fail_start)
        assert fail_ticket.wait()["status"] == "error"
        fail_entry = _rag_inflight._by_id[fail_ticket.search_id]
        finished = fail_entry.finished_at
        assert finished is not None

        clock.set(finished + 29.0)
        poll = attach_search(fail_ticket.search_id)
        assert poll.cache_hit is False
        assert poll.wait()["status"] == "error"

        clock.set(finished + 31.0)
        with pytest.raises(UnknownSearchError):
            attach_search(fail_ticket.search_id)

        _clear_registry()
        clock.set(20_000.0)

        def _ok_start() -> dict[str, Any]:
            return {"status": "ok", "context": "cached body"}

        ok_ticket = admit_search("success-ttl-key", _ok_start)
        assert ok_ticket.wait()["status"] == "ok"
        ok_finished = _rag_inflight._by_id[ok_ticket.search_id].finished_at
        assert ok_finished is not None

        clock.set(ok_finished + 31.0)
        ok_poll = attach_search(ok_ticket.search_id)
        assert ok_poll.cache_hit is True
        assert ok_poll.wait()["context"] == "cached body"

        clock.set(ok_finished + 601.0)
        with pytest.raises(UnknownSearchError):
            attach_search(ok_ticket.search_id)
    finally:
        _clear_registry()
