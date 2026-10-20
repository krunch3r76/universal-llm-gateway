"""Compact ``rag_search`` projection stays under the KERNEL-H byte budget."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_MCP = Path(__file__).resolve().parents[1]
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

from tools import _rag_search_exec  # noqa: E402
from tools._rag_inflight import admit_search, search_key  # noqa: E402
from tools._rag_relay_options import map_search_tool_options  # noqa: E402
from tools._rag_search_projection import (  # noqa: E402
    COMPACT_MAX_BYTES,
    project_rag_search_envelope,
)


def _json_bytes(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _large_context() -> str:
    blocks = []
    for idx in range(12):
        blocks.append(
            f"[Source: docs/research/prompting/paper-{idx}.md]\n\n"
            + ("Retrieval corpus paragraph with placement and hierarchy guidance. " * 40)
        )
    body = "\n\n---\n\n".join(blocks)
    assert len(body.encode("utf-8")) > 5120
    return body


def test_large_context_compact_under_budget_full_keeps_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full context >5 KB; compact JSON ≤4500 B with signals; full keeps context.

    Breaks when: pipeline returns huge ``context`` but MCP forwards it verbatim on
    every ``rag(op=search)`` call (KERNEL-H hop on retrieval-before-authoring).
    """
    huge = _large_context()

    def fake_pipeline_call(model, messages, *, pipeline_options, timeout):
        return {
            "choices": [{"message": {"content": huge}}],
            "pipeline": {
                "retrieval": {
                    "chunks_found": 12,
                    "scope_rejected": False,
                    "scope_source": "user_override",
                    "resolved_scope": "llm_prompting",
                    "weak_match": False,
                    "top_relevance": 0.9123,
                    "rerank_status": "ok",
                    "weak_match_basis": "cross_encoder",
                    "chunks": [
                        {
                            "rank": i,
                            "chunk_id": f"id{i:04d}",
                            "source": f"docs/research/prompting/paper-{i}.md",
                            "ce_score": 0.91 - i * 0.01,
                        }
                        for i in range(1, 11)
                    ],
                }
            },
        }

    monkeypatch.setattr(_rag_search_exec, "pipeline_call", fake_pipeline_call)
    monkeypatch.setattr(_rag_search_exec, "rag_pipeline_timeout", lambda _model: 1.0)

    pipeline_options = map_search_tool_options(
        scope_override="llm_prompting",
        prefixes=None,
        top_k=10,
    )
    ticket = admit_search(
        search_key("prompt placement", pipeline_options),
        lambda: _rag_search_exec.run_rag_search(
            "prompt placement",
            scope="llm_prompting",
            prefixes=None,
            pipeline_options=pipeline_options,
            unscoped=False,
        ),
    )
    full = project_rag_search_envelope(
        ticket.stamp(ticket.wait()),
        projection="full",
    )
    compact = project_rag_search_envelope(
        ticket.stamp(ticket.wait()),
        projection="compact",
    )

    assert full["status"] == "ok"
    assert full["context"] == huge
    assert full["search_id"].startswith("rs-")
    assert full["retrieval"]["weak_match"] is False
    assert full["retrieval"]["top_relevance"] == 0.9123

    assert "context" not in compact
    assert compact["status"] == "ok"
    assert compact["search_id"] == full["search_id"]
    assert compact["retrieval"]["weak_match"] is False
    assert compact["retrieval"]["top_relevance"] == 0.9123
    assert _json_bytes(compact) <= COMPACT_MAX_BYTES
