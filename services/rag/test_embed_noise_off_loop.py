"""Embed-phase noise tagging must not block the asyncio loop (friction:39420 r4).

The chunk is the pathological citation line. ``noise_reason`` is the real
function. A 50 ms heartbeat must keep firing for the whole call: the old
``.*``-wrapped pattern holds the GIL for ~1 s (about one tick); the rewritten
pattern returns before a stall can form.
"""

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

# One comma-heavy line. Leading/trailing ``.*`` makes ``re.search`` quadratic
# and holds the GIL for the whole match (~1 s). Without those wrappers the
# same line is sub-millisecond.
_PATHOLOGICAL = ("a, " * 3500) + "x"
_HEARTBEAT_S = 0.05
_STALL_S = 0.20
_MIN_TICKS_IF_SLOW = 5


def _chunk(text: str) -> Chunk:
    return Chunk(text=text, metadata={})


@pytest.mark.asyncio
async def test_pathological_noise_reason_keeps_loop_heartbeat() -> None:
    """Unmocked noise_reason on the pathological line must not freeze the loop."""
    path = Path("/tmp/r4-noise-off-loop.md")
    source = str(path.resolve())
    path_key = compute_path_key(source)
    chunks = [_chunk(_PATHOLOGICAL)]
    chunk_id = compose_chunk_id(path_key, compute_chunk_hash(0, chunks[0].text))
    config = MagicMock()
    config.contextualize_model = "test-model"
    config.contextualize_client_timeout_s = 30.0
    event_bus = MagicMock()
    event_bus.publish_nowait = AsyncMock()

    ticks = 0
    finished = False

    async def _heartbeat() -> None:
        nonlocal ticks
        while not finished:
            await asyncio.sleep(_HEARTBEAT_S)
            if not finished:
                ticks += 1

    async def _run_embed() -> None:
        nonlocal finished
        try:
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
        finally:
            finished = True

    t0 = time.perf_counter()
    with patch(
        "services.rag.rag_service.indexing.embed._run_contextualization_phase",
        new_callable=AsyncMock,
        return_value=(["ctx"], [], [True]),
    ):
        await asyncio.gather(_run_embed(), _heartbeat())
    elapsed = time.perf_counter() - t0
    # A call that outlasts several heartbeats while holding the GIL records
    # almost no ticks. A call that returns before _STALL_S never froze the loop.
    if elapsed >= _STALL_S:
        assert ticks >= _MIN_TICKS_IF_SLOW, (
            f"loop heartbeat stalled elapsed={elapsed:.3f}s ticks={ticks}"
        )
    assert elapsed < 5.0, (
        f"noise_reason still too slow elapsed={elapsed:.3f}s ticks={ticks}"
    )
