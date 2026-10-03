from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.rag import contextualize
from services.rag.chunkers import Chunk


def _chunk(text: str) -> Chunk:
    return Chunk(text=text, metadata={})


def _pipeline_http_response(items: list[object]) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(items)}}]
    }
    return mock_resp


@pytest.mark.asyncio
async def test_contextualize_abandons_tail_after_success_threshold(
    monkeypatch: Any,
) -> None:
    """Partial pipeline success: caller records failed chunks; no tail-abandon seam.

    Tail-idle / min_success_threshold policy lives in rag-contextualize MapExecutor.
    At this layer, a null iteration is a failed chunk and abandoned_indices stays empty.
    """
    mock_post = AsyncMock(
        return_value=_pipeline_http_response(
            [
                {"context": "context for fast one"},
                {"context": "context for fast two"},
                None,
            ]
        )
    )
    monkeypatch.setattr(contextualize, "_CLIENT", MagicMock(post=mock_post))

    result = await contextualize.contextualize_chunks(
        [_chunk("fast one"), _chunk("fast two"), _chunk("slow chunk")],
        "/tmp/source.md",
        "model",
        chunk_indices=[10, 11, 12],
    )

    assert result.successful_count == 2
    assert result.failed_indices == [12]
    assert result.abandoned_indices == []
    assert result.failure_reprs[12] == "missing_iteration_output"
    mock_post.assert_awaited_once()
    posted = mock_post.await_args.kwargs
    assert posted["json"]["model"] == "rag-contextualize"
    chunk_payloads = posted["json"]["pipeline_options"]["chunks"]
    assert len(chunk_payloads) == 3
    assert "slow chunk" in chunk_payloads[2]["user_msg"]


@pytest.mark.asyncio
async def test_contextualize_waits_until_success_threshold(monkeypatch: Any) -> None:
    """Full pipeline success: all chunk iterations return context (threshold met)."""
    mock_post = AsyncMock(
        return_value=_pipeline_http_response(
            [
                {"context": "context for fast"},
                {"context": "context for later one"},
                {"context": "context for later two"},
            ]
        )
    )
    monkeypatch.setattr(contextualize, "_CLIENT", MagicMock(post=mock_post))

    result = await contextualize.contextualize_chunks(
        [_chunk("fast"), _chunk("later one"), _chunk("later two")],
        "/tmp/source.md",
        "model",
    )

    assert result.successful_count == 3
    assert result.failed_indices == []
    assert result.abandoned_indices == []
    assert result.contexts == [
        "context for fast",
        "context for later one",
        "context for later two",
    ]
