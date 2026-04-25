"""LLM-based contextual embedding enrichment for RAG chunks.

Generates a short context prefix per chunk that disambiguates its content
within the parent document. The prefix is prepended to the chunk text
*only* for embedding — stored document text remains the original.

This follows the "contextual retrieval" pattern: chunks that share
overlapping vocabulary (e.g. "knowledge graph" appearing in both PKB
schema papers and enterprise KG surveys) get distinct embedding vectors
because their context prefixes anchor them to different documents.

Architecture:
  - Workers send chunk requests directly to Stargate. Stargate is the sole
    authority over model loading; if the contextualize_model is cold, the
    request blocks until Stargate loads it on demand.
  - Stampede protection has three layers, each strictly optimization:
      1. ``max_concurrency`` bounds in-flight workers per file.
      2. ``global_gate`` (FifoCapacityGate) bounds total in-flight requests
         across all concurrent files.
      3. ``coordinator`` pauses new acquisitions while the contextualize
         model is mid-load (subscribed to Stargate's `model.loading.started`
         coordination signal — see contextualize_coordinator.py).
    None of these are correctness gates. Per the stargate-model-lifecycle
    invariant, callers MUST proceed when coordination signals are
    unavailable; the per-chunk ``client_timeout_s`` is the correctness
    backstop.
  - X-Request-Timeout header enforces per-chunk inference deadline from the
    moment Stargate acquires the slot, not from enqueue time.
  - Per-chunk failures propagate so indexing fails loudly. Silently
    embedding chunks without a context prefix is a retrieval-quality
    regression, not a graceful degradation.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
from typing import TYPE_CHECKING

from transport_utils import DEFAULT_STARGATE_URL, make_async_client

from services.rag.chunkers import Chunk

if TYPE_CHECKING:
    from universal_concurrency import FifoCapacityGate

    from services.rag.contextualize_coordinator import ContextualizeModelCoordinator

logger = logging.getLogger(__name__)

# TODO: Detect Persian/Arabic-script chunks and request Farsi context prefixes.
# Mixed-language prefixes are acceptable for now because embedding quality remains
# good enough for scoped retrieval, but poetry corpora should not stay English-led.
_CONTEXT_SYSTEM_PROMPT = (
    "You are a search indexing assistant. Given a chunk from a document "
    "and surrounding context, write a short succinct context (2-3 sentences, "
    "under 100 tokens) to situate this chunk within the overall document "
    "for the purposes of improving search retrieval. Focus on: which document "
    "this is from, what specific topic the chunk covers, and resolving any "
    "ambiguous references (pronouns, abbreviations, 'the method', etc.). "
    "Output ONLY the context sentences, nothing else."
)

_NEIGHBOR_CHARS = 800


def _build_chunk_context(
    idx: int,
    chunks: list[Chunk],
    source: str,
) -> str:
    """Build a user message with document skeleton + neighboring chunk excerpts.

    Provides the LLM with enough global and local context to generate a
    chunk-specific disambiguation prefix (Anthropic's proven approach).
    Budget: ~3-4k tokens for context, ~1k for the target chunk, leaving
    headroom within the 8k context window.
    """
    chunk = chunks[idx]
    text = chunk.text[:6000]

    # Neighboring chunk excerpts for local continuity
    prev_excerpt = ""
    if idx > 0:
        prev_excerpt = chunks[idx - 1].text[-_NEIGHBOR_CHARS:]
    next_excerpt = ""
    if idx < len(chunks) - 1:
        next_excerpt = chunks[idx + 1].text[:_NEIGHBOR_CHARS]

    parts = [f"Document: {source}"]
    if prev_excerpt:
        parts.append(f"[Previous chunk excerpt]\n{prev_excerpt}")
    parts.append(f"[TARGET CHUNK]\n{text}")
    if next_excerpt:
        parts.append(f"[Next chunk excerpt]\n{next_excerpt}")
    return "\n\n".join(parts)


class ContextualizationError(RuntimeError):
    """Raised when one or more chunks failed to contextualize.

    Indexing must fail loudly when contextualization is configured but cannot
    complete — silently embedding chunks without context prefixes degrades
    retrieval quality without any visible signal. The reconcile sweep retries
    failed files; transient failures (cold load, eviction) eventually succeed.
    """


async def contextualize_chunks(
    chunks: list[Chunk],
    source: str,
    model: str,
    *,
    timeout_s: float = 30.0,
    max_concurrency: int = 32,
    client_timeout_s: float = 60.0,
    global_gate: FifoCapacityGate | None = None,
    coordinator: ContextualizeModelCoordinator | None = None,
) -> list[str]:
    """Generate context prefixes for chunks via LLM.

    Each context is a 50-100 token disambiguation prefix (per Anthropic's
    contextual retrieval findings). Neighboring chunk excerpts are included
    to help the LLM resolve references and identify structural position.

    Workers send requests directly to Stargate; Stargate handles model loading
    transparently per the stargate-model-lifecycle invariant. Stampede
    protection comes from ``max_concurrency`` (per-file), the optional
    ``global_gate`` (cross-file), and the optional ``coordinator`` (pauses
    new acquisitions while the model is actively cold-loading).

    Per-chunk failures propagate as :class:`ContextualizationError` after
    workers drain — silent degradation to no-context embeddings is a
    retrieval-quality regression and not allowed.

    Args:
        chunks: Chunks to contextualize.
        source: Source file path (included in the prompt for document identity).
        model: Model ID for context generation (e.g. qwen3-5-9b-q8-0-262144).
        timeout_s: Per-chunk inference timeout in seconds, enforced server-side
            via X-Request-Timeout (starts when Stargate acquires the model slot,
            not when the request is enqueued).
        max_concurrency: Maximum number of in-flight contextualization requests
            for this file.
        client_timeout_s: Outer HTTP timeout covering queue wait and inference.
            Must be wide enough to cover Stargate's load-on-demand window for
            a cold model.
        global_gate: Optional cross-file capacity gate. When provided, each
            worker acquires a slot before calling the LLM, bounding total
            in-flight contextualization requests across all concurrent files.
        coordinator: Optional batch-pipeline coordinator subscribed to
            Stargate's model lifecycle signals. When provided, each worker
            awaits the coordinator's "model believed available" hint before
            acquiring the global gate — pure optimization, never a
            correctness gate.

    Returns:
        List of context strings (one per chunk).

    Raises:
        ContextualizationError: One or more chunks failed; the file should be
            re-attempted (the reconcile sweep handles retries).
    """
    if not chunks:
        return []

    results: list[str] = [""] * len(chunks)
    failures: list[tuple[int, BaseException]] = []
    worker_count = max(1, min(max_concurrency, len(chunks)))
    queue: asyncio.Queue[int | None] = asyncio.Queue()
    for idx in range(len(chunks)):
        queue.put_nowait(idx)

    async def _worker(worker_id: int) -> None:
        while True:
            idx = await queue.get()
            try:
                if idx is None:
                    return
                try:
                    if coordinator is not None:
                        await coordinator.wait_for_available(
                            model, timeout=client_timeout_s
                        )
                    if global_gate is not None:
                        await global_gate.acquire(
                            f"ctx-{source}-{idx}",
                            timeout=client_timeout_s,
                        )
                    try:
                        user_msg = _build_chunk_context(idx, chunks, source)
                        context = await _call_llm(
                            user_msg,
                            model,
                            timeout_s,
                            client_timeout_s=client_timeout_s,
                        )
                        results[idx] = context
                    finally:
                        if global_gate is not None:
                            await global_gate.release()
                except Exception as exc:
                    failures.append((idx, exc))
                    logger.warning(
                        "Contextualization failed for chunk %d of %s: %r",
                        idx,
                        source,
                        exc,
                    )
            finally:
                queue.task_done()

    workers = [
        asyncio.create_task(_worker(i), name=f"contextualize-worker-{i}")
        for i in range(worker_count)
    ]
    await queue.join()
    for _ in workers:
        queue.put_nowait(None)
    await asyncio.gather(*workers)

    successful = sum(1 for r in results if r)
    logger.info(
        "Contextualized %d/%d chunks for %s (model=%s, concurrency=%d)",
        successful,
        len(chunks),
        source,
        model,
        worker_count,
    )
    if failures:
        first_idx, first_exc = failures[0]
        raise ContextualizationError(
            f"contextualization failed for {len(failures)}/{len(chunks)} chunks "
            f"of {source} (first failure: chunk {first_idx}: {first_exc!r})"
        ) from first_exc
    return results


# Timeout is supplied per call so config can shrink the queue wait budget
# without rebuilding the shared client.
_CLIENT = make_async_client(DEFAULT_STARGATE_URL, timeout=None)

# Anthropic research: 50-100 token prefixes are the sweet spot for 1024-token chunks.
_MAX_CONTEXT_TOKENS = 150


# Bump only when invalidation must cross a refactor that does NOT change the
# sources hashed below (e.g. contributor intent diverges from code change).
_CONTEXTUALIZE_ALGORITHM_VERSION = "1"


def build_contextualize_schema_version() -> str:
    """Return a stable version covering prompt text, neighbor budgets, and chunk-context assembly source."""
    material = "\n".join(
        [
            _CONTEXTUALIZE_ALGORITHM_VERSION,
            _CONTEXT_SYSTEM_PROMPT,
            str(_NEIGHBOR_CHARS),
            str(_MAX_CONTEXT_TOKENS),
            inspect.getsource(_build_chunk_context),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


CONTEXTUALIZE_PROMPT_HASH: str = build_contextualize_schema_version()


async def _call_llm(
    user_msg: str,
    model: str,
    timeout_s: float,
    *,
    client_timeout_s: float,
) -> str:
    """Call the contextualization LLM for a single chunk.

    timeout_s is passed as X-Request-Timeout so Stargate starts the clock when
    inference begins, not when the request enters the queue. The httpx client
    timeout is a wide ceiling covering queue wait + inference time.

    Returns the context string, or empty string on failure.
    """
    response = await _CLIENT.post(
        "/v1/chat/completions",
        headers={"X-Request-Timeout": str(timeout_s)},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": _CONTEXT_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": _MAX_CONTEXT_TOKENS,
            "temperature": 0.1,
        },
        timeout=client_timeout_s,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return content.strip() if isinstance(content, str) else ""
