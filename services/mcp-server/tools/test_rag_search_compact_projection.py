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


def _context_block(idx: int, *, body_repeat: int = 40) -> str:
    return (
        f"[Source: docs/research/prompting/paper-{idx}.md | Last changed: 2026-01-01]\n"
        f"[Body evidence]\n"
        + ("Retrieval corpus paragraph with placement and hierarchy guidance. " * body_repeat)
    )


def _large_context(*, blocks: int = 12, body_repeat: int = 40) -> str:
    blocks_list = [_context_block(i, body_repeat=body_repeat) for i in range(blocks)]
    body = "\n\n---\n\n".join(blocks_list)
    assert len(body.encode("utf-8")) > 5120
    return body


def _chunk_rows(count: int) -> list[dict[str, Any]]:
    return [
        {
            "rank": i,
            "chunk_id": f"id{i:04d}",
            "source": f"docs/research/prompting/paper-{i}.md",
            "prior_score": 0.95 - i * 0.001,
            "ce_score": 0.91 - i * 0.01,
            "final_score": 0.90 - i * 0.01,
        }
        for i in range(1, count + 1)
    ]


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
                    "chunks": _chunk_rows(10),
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
    preview = compact["context_preview"]
    assert preview
    assert preview.startswith("[Source: docs/research/prompting/paper-0.md")
    assert "placement and hierarchy guidance" in preview


def test_top_k_20_compact_nonempty_preview_before_row_trim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``top_k`` 20 with heavy ``retrieval.chunks[]`` stays ≤4500 B; preview survives.

    Breaks when: tail row trim runs only after preview is emptied (F2 ordering bug).
    """
    huge = _large_context(blocks=20, body_repeat=35)

    def fake_pipeline_call(model, messages, *, pipeline_options, timeout):
        return {
            "choices": [{"message": {"content": huge}}],
            "pipeline": {
                "retrieval": {
                    "chunks_found": 20,
                    "scope_rejected": False,
                    "scope_source": "user_override",
                    "resolved_scope": "llm_prompting",
                    "weak_match": False,
                    "top_relevance": 0.88,
                    "rerank_status": "ok",
                    "weak_match_basis": "cross_encoder",
                    "chunks": _chunk_rows(20),
                }
            },
        }

    monkeypatch.setattr(_rag_search_exec, "pipeline_call", fake_pipeline_call)
    monkeypatch.setattr(_rag_search_exec, "rag_pipeline_timeout", lambda _model: 1.0)

    pipeline_options = map_search_tool_options(
        scope_override="llm_prompting",
        prefixes=None,
        top_k=20,
    )
    ticket = admit_search(
        search_key("instruction hierarchy", pipeline_options),
        lambda: _rag_search_exec.run_rag_search(
            "instruction hierarchy",
            scope="llm_prompting",
            prefixes=None,
            pipeline_options=pipeline_options,
            unscoped=False,
        ),
    )
    compact = project_rag_search_envelope(
        ticket.stamp(ticket.wait()),
        projection="compact",
    )

    assert _json_bytes(compact) <= COMPACT_MAX_BYTES
    preview = compact.get("context_preview")
    assert preview
    assert isinstance(preview, str) and preview.strip()
    assert "[Source:" in preview
    truncated = compact.get("chunks_truncated")
    assert truncated is None or truncated >= 0


def test_in_flight_and_error_envelopes_unchanged_under_compact() -> None:
    """``in_flight`` and bare ``error`` envelopes pass through without projection fields.

    Breaks when: compact projection mutates poll handles or transport errors.
    """
    in_flight = {
        "status": "in_flight",
        "pipeline": "rag-search",
        "search_id": "rs-test-inflight",
        "elapsed_s": 90.0,
        "wait_budget_s": 90.0,
        "poll": "rag(op=search, search_id=…)",
    }
    err = {"error": "Unknown or expired search_id 'rs-gone'", "search_id": "rs-gone"}

    assert project_rag_search_envelope(in_flight, projection="compact") == in_flight
    assert project_rag_search_envelope(err, projection="compact") == err


def test_search_id_full_returns_context_mapped_hit_compact() -> None:
    """Same ``search_id`` with ``projection=full`` keeps ``context``; mapped packs compact.

    Breaks when: poll re-fetch drops chunk text or mapped hits skip compact bounds.
    """
    context = _context_block(0, body_repeat=50)
    envelope: dict[str, Any] = {
        "status": "ok",
        "pipeline": "rag-search",
        "content_length": len(context),
        "duration_s": 0.1,
        "context": context,
        "search_id": "rs-mapped-test",
        "retrieval": {
            "chunks_found": 1,
            "resolved_scope": "claude_api",
            "scope_source": "user_override",
            "weak_match": False,
            "top_relevance": 0.95,
            "chunks": _chunk_rows(1),
        },
    }

    full = project_rag_search_envelope(envelope, projection="full")
    compact = project_rag_search_envelope(envelope, projection="compact")

    assert full["context"] == context
    assert "context" not in compact
    assert compact["projection"] == "compact"
    assert compact["context_preview"]
    assert _json_bytes(compact) <= COMPACT_MAX_BYTES
