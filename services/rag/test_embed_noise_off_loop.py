"""Embed-phase noise tagging must not block the asyncio loop (friction:39420 r4)."""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.rag.chunkers import Chunk
from services.rag.rag_service.indexing.embed import _run_embed_phase
from services.rag.rag_service.indexing.embed_diff import (
    compose_chunk_id,
    compute_chunk_hash,
    compute_path_key,
)


def _chunk(text: str) -> Chunk:
    return Chunk(text=text, metadata={})


@pytest.mark.asyncio
async def test_slow_noise_reason_does_not_block_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocking noise_reason on a worker thread still lets other coroutines run."""
    import services.rag.rag_service.indexing.embed as embed_mod

    def _slow_noise(_text: str) -> None:
        time.sleep(0.35)
        return None

    monkeypatch.setattr(embed_mod, "noise_reason", _slow_noise)

    path = Path("/tmp/r4-noise-off-loop.md")
    source = str(path.resolve())
    path_key = compute_path_key(source)
    chunks = [_chunk("body")]
    chunk_id = compose_chunk_id(path_key, compute_chunk_hash(0, chunks[0].text))
    config = MagicMock()
    config.contextualize_model = "test-model"
    config.contextualize_client_timeout_s = 30.0
    event_bus = MagicMock()
    event_bus.publish_nowait = AsyncMock()

    ticks = 0

    async def _heartbeat() -> None:
        nonlocal ticks
        for _ in range(12):
            await asyncio.sleep(0.05)
            ticks += 1

    async def _run_embed() -> None:
        await _run_embed_phase(
            file_path=path,
            source=source,
            source_hash=hashlib.sha256(b"x").hexdigest(),
            chunks=chunks,
            existing_ids=[chunk_id],
            existing_timestamps={},
            metadata_overrides=None,
            prop_index=None,
            collection=MagicMock(),
            chroma_client=MagicMock(),
            event_bus=event_bus,
            config=config,
            correlation_id="corr-r4",
            operation=None,
        )

    with patch(
        "services.rag.rag_service.indexing.embed._run_contextualization_phase",
        new_callable=AsyncMock,
        return_value=(["ctx"], [], [True]),
    ):
        await asyncio.gather(_run_embed(), _heartbeat())

    assert ticks >= 5, "expected concurrent coroutines while noise_reason ran off-loop"
