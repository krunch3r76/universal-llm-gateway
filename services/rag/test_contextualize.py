from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services.rag import contextualize
from services.rag.chunkers import Chunk


def _chunk(text: str) -> Chunk:
    return Chunk(text=text, metadata={})


@pytest.mark.asyncio
async def test_contextualize_abandons_tail_after_success_threshold(
    monkeypatch: Any,
) -> None:
    async def fake_call_llm(
        user_msg: str,
        model: str,
        timeout_s: float,
        *,
        client_timeout_s: float,
        request_id: str,
    ) -> str:
        if "[TARGET CHUNK]\nslow chunk" in user_msg:
            await asyncio.sleep(10)
        return f"context:{request_id}"

    diagnostics: list[tuple[str, int, str]] = []

    async def diagnostics_sink(
        event: str,
        *,
        chunk_index: int,
        request_id: str,
        duration_seconds: float | None = None,
        error: str | None = None,
        output_chars: int | None = None,
    ) -> None:
        diagnostics.append((event, chunk_index, request_id))

    monkeypatch.setattr(contextualize, "_call_llm", fake_call_llm)
    monkeypatch.setattr(
        contextualize, "_cancel_llm_request", lambda request_id, model: _async_true()
    )

    result = await contextualize.contextualize_chunks(
        [_chunk("fast one"), _chunk("fast two"), _chunk("slow chunk")],
        "/tmp/source.md",
        "model",
        max_concurrency=3,
        tail_idle_timeout_s=0.01,
        tail_min_success_ratio=0.5,
        chunk_indices=[10, 11, 12],
        diagnostics_sink=diagnostics_sink,
    )

    assert result.successful_count == 2
    assert result.failed_indices == [12]
    assert result.abandoned_indices == [12]
    assert "ContextualizationTailAbandoned" in result.failure_reprs[12]
    assert "cancel_requested=True" in result.failure_reprs[12]
    assert {event for event, _, _ in diagnostics} >= {
        "started",
        "completed",
        "abandoned",
    }


@pytest.mark.asyncio
async def test_contextualize_waits_until_success_threshold(monkeypatch: Any) -> None:
    async def fake_call_llm(
        user_msg: str,
        model: str,
        timeout_s: float,
        *,
        client_timeout_s: float,
        request_id: str,
    ) -> str:
        if "later" in user_msg:
            await asyncio.sleep(0.03)
        return f"context:{request_id}"

    monkeypatch.setattr(contextualize, "_call_llm", fake_call_llm)

    result = await contextualize.contextualize_chunks(
        [_chunk("fast"), _chunk("later one"), _chunk("later two")],
        "/tmp/source.md",
        "model",
        max_concurrency=3,
        tail_idle_timeout_s=0.01,
        tail_min_success_ratio=1.0,
    )

    assert result.successful_count == 3
    assert result.failed_indices == []
    assert result.abandoned_indices == []


async def _async_true() -> bool:
    return True
