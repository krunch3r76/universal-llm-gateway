"""rag_search pipeline_options must match the rag-context relay contract."""

from __future__ import annotations

import sys
from pathlib import Path

_MCP = Path(__file__).resolve().parents[1]
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

from systems.pipeline.core.step_controls import (  # noqa: E402
    RAG_CONTEXT_STEP_CONTROLS_ERROR,
    finalize_relay_pipeline_options,
)

from tools import _rag_search_exec  # noqa: E402

_OK = {
    "choices": [{"message": {"content": "[Source: x]\nbody"}}],
    "pipeline": {
        "retrieval": {
            "resolved_scope": "code_retrieval",
            "chunks_found": 1,
            "scope_rejected": False,
            "scope_source": "user_override",
        }
    },
}


def test_rag_context_preserves_legacy_enable_flags() -> None:
    raw = {
        "hyde_enabled": True,
        "rerank_enabled": False,
        "include_retrieval_metadata": True,
    }
    finalized, err = finalize_relay_pipeline_options("rag-context", raw)
    assert err is None
    assert finalized is not None
    assert finalized["hyde_enabled"] is True
    assert finalized["rerank_enabled"] is False
    assert "step_overrides" not in finalized


def test_rag_context_rejects_step_overrides() -> None:
    finalized, err = finalize_relay_pipeline_options(
        "rag-context",
        {"step_overrides": {"rerank": {"enabled": False}}},
    )
    assert finalized is None
    assert err == RAG_CONTEXT_STEP_CONTROLS_ERROR


def test_rag_context_rejects_skip_steps() -> None:
    finalized, err = finalize_relay_pipeline_options(
        "rag-context",
        {"skip_steps": ["rerank"]},
    )
    assert finalized is None
    assert err == RAG_CONTEXT_STEP_CONTROLS_ERROR


def test_hyde_and_rerank_flags_reach_rag_context_call(monkeypatch) -> None:
    captured: dict = {}

    def fake_pipeline_call(
        model: str,
        _messages: list,
        *,
        pipeline_options: dict | None = None,
        timeout: float,
    ) -> dict:
        captured["model"] = model
        captured["pipeline_options"] = dict(pipeline_options or {})
        return _OK

    monkeypatch.setattr(_rag_search_exec, "pipeline_call", fake_pipeline_call)

    options, err = finalize_relay_pipeline_options(
        "rag-context",
        {
            "hyde_enabled": True,
            "rerank_enabled": False,
            "include_retrieval_metadata": True,
        },
    )
    assert err is None
    assert options is not None

    _rag_search_exec.run_rag_search(
        "flag contract probe",
        scope="code_retrieval",
        prefixes=None,
        pipeline_options=options,
        unscoped=False,
    )

    assert captured["model"] == "rag-context"
    assert captured["pipeline_options"]["hyde_enabled"] is True
    assert captured["pipeline_options"]["rerank_enabled"] is False
    assert "generate_hyde" not in captured["pipeline_options"].get(
        "step_overrides", {}
    )


def test_step_overrides_do_not_call_pipeline(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_pipeline_call(*_args, **_kwargs) -> dict:
        calls["n"] += 1
        return _OK

    monkeypatch.setattr(_rag_search_exec, "pipeline_call", fake_pipeline_call)

    _options, err = finalize_relay_pipeline_options(
        "rag-context",
        {"step_overrides": {"rerank": {"enabled": True}}},
    )
    assert err == RAG_CONTEXT_STEP_CONTROLS_ERROR
    assert calls["n"] == 0
