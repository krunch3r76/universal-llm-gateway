"""Hermetic relay contract for rag(op=search) → rag-search."""

from __future__ import annotations

import sys
from pathlib import Path

_MCP = Path(__file__).resolve().parents[1]
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

from tools import _rag_search_exec  # noqa: E402
from tools._rag_relay_options import (  # noqa: E402
    RAG_CONTEXT_STEP_CONTROLS_ERROR,
    finalize_relay_pipeline_options,
    map_search_tool_options,
)


def test_scope_and_top_k_map_onto_pipeline_options() -> None:
    mapped = map_search_tool_options(
        scope_override="claude_api",
        prefixes=None,
        top_k=3,
    )
    assert mapped["scope_override"] == "claude_api"
    assert mapped["rag_max_chunks"] == 3
    assert "scope" not in mapped
    assert "top_k" not in mapped
    default_k = map_search_tool_options(
        scope_override="llm_prompting",
        prefixes=None,
        top_k=20,
    )
    assert default_k["rag_max_chunks"] == 20


def test_rag_context_rejects_step_controls_and_keeps_raw_flags() -> None:
    prepared, err = finalize_relay_pipeline_options(
        "rag-context",
        {"hyde_enabled": True, "rerank_enabled": False, "scope_override": "claude_api"},
    )
    assert err is None
    assert prepared == {
        "hyde_enabled": True,
        "rerank_enabled": False,
        "scope_override": "claude_api",
    }
    prepared, err = finalize_relay_pipeline_options(
        "rag-context",
        {"step_overrides": {"rerank": {"enabled": False}}},
    )
    assert prepared is None
    assert err == RAG_CONTEXT_STEP_CONTROLS_ERROR
    prepared, err = finalize_relay_pipeline_options(
        "rag-context",
        {"skip_steps": ["rerank"]},
    )
    assert prepared is None
    assert err == RAG_CONTEXT_STEP_CONTROLS_ERROR


def test_rag_search_folds_flags_and_posts_that_model(monkeypatch) -> None:
    captured: dict = {}

    def fake_pipeline_call(model, messages, *, pipeline_options, timeout):
        captured["model"] = model
        captured["options"] = pipeline_options
        return {
            "choices": [{"message": {"content": "[Source: a.md]\n\nbody"}}],
            "pipeline": {
                "retrieval": {
                    "chunks_found": 1,
                    "scope_rejected": False,
                    "scope_source": "user_override",
                    "resolved_scope": "claude_api",
                }
            },
        }

    monkeypatch.setattr(_rag_search_exec, "pipeline_call", fake_pipeline_call)
    monkeypatch.setattr(_rag_search_exec, "rag_pipeline_timeout", lambda _model: 1.0)
    env = _rag_search_exec.run_rag_search(
        "context windows",
        scope="claude_api",
        prefixes=None,
        pipeline_options=map_search_tool_options(
            scope_override="claude_api",
            prefixes=None,
            top_k=3,
            hyde_enabled=True,
            rerank_enabled=False,
            step_overrides={"rerank": {"enabled": True}},
            skip_steps=["generate_hyde"],
        ),
        unscoped=False,
    )
    assert env["pipeline"] == "rag-search"
    assert captured["model"] == "rag-search"
    options = captured["options"]
    assert options["scope_override"] == "claude_api"
    assert options["rag_max_chunks"] == 3
    assert "hyde_enabled" not in options
    assert "rerank_enabled" not in options
    assert options["step_overrides"]["generate_hyde"]["enabled"] is True
    assert options["step_overrides"]["rerank"]["enabled"] is False
    assert options["skip_steps"] == ["generate_hyde"]
