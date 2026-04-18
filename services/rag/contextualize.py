"""LLM-based contextual embedding enrichment for RAG chunks.

Generates a short context prefix per chunk that disambiguates its content
within the parent document. The prefix is prepended to the chunk text
*only* for embedding — stored document text remains the original.

This follows the "contextual retrieval" pattern: chunks that share
overlapping vocabulary (e.g. "knowledge graph" appearing in both PKB
schema papers and enterprise KG surveys) get distinct embedding vectors
because their context prefixes anchor them to different documents.

Architecture:
  - Before dispatching the full batch, a single probe request is sent with a
    tight client timeout (ctx_probe_timeout_s). If the probe succeeds the model
    is warm and the full batch fires immediately. On retryable failure (httpx
    timeout or 503/429) the probe is retried with exponential backoff until the
    model is reachable or ctx_probe_max_probes is exhausted.
  - After a successful probe, the full batch (up to max_concurrency workers)
    fires normally. Each worker uses client_timeout_s as the outer HTTP timeout.
  - X-Request-Timeout header enforces per-chunk inference deadline from the
    moment Stargate acquires the slot, not from enqueue time.
  - Returns context strings; empty string on per-chunk failure (graceful).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
from typing import TYPE_CHECKING

import httpx

from services.rag.chunkers import Chunk

if TYPE_CHECKING:
    from universal_concurrency import FifoCapacityGate

logger = logging.getLogger(__name__)

STARGATE_URL = "http://localhost:9999"

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

# Retryable HTTP status codes — model is busy or overloaded but will recover.
_RETRYABLE_STATUS_CODES = frozenset({429, 503})


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


async def _probe_model(
    model: str,
    timeout_s: float,
    inference_timeout_s: float,
    *,
    backoff_initial_s: float,
    backoff_max_s: float,
    max_probes: int,
) -> bool:
    """Test model readiness with a minimal request before committing the full batch.

    Sends a single short probe with a tight client timeout. On retryable failure
    (httpx.TimeoutException or 503/429) waits with exponential backoff and retries.
    Returns True when the model responds, False after max_probes exhausted.

    ∀ retryable_failure: wait exponential_backoff, retry; never more than max_probes.
    ∀ non_retryable_failure (4xx except 429): return False immediately.
    """
    if max_probes <= 0:
        return True  # Probe disabled; proceed directly to full batch.

    backoff = backoff_initial_s
    for attempt in range(1, max_probes + 1):
        try:
            await _call_llm(
                "ping",
                model,
                inference_timeout_s,
                client_timeout_s=timeout_s,
            )
            logger.debug(
                "Contextualization probe succeeded (model=%s, attempt=%d)",
                model,
                attempt,
            )
            return True
        except httpx.TimeoutException:
            logger.info(
                "Contextualization probe timeout (model=%s, attempt=%d/%d, "
                "next_wait=%.1fs)",
                model,
                attempt,
                max_probes,
                backoff,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in _RETRYABLE_STATUS_CODES:
                logger.info(
                    "Contextualization probe got %d (model=%s, attempt=%d/%d, "
                    "next_wait=%.1fs)",
                    exc.response.status_code,
                    model,
                    attempt,
                    max_probes,
                    backoff,
                )
            else:
                logger.warning(
                    "Contextualization probe non-retryable %d (model=%s)",
                    exc.response.status_code,
                    model,
                )
                return False
        except Exception as exc:
            logger.warning(
                "Contextualization probe unexpected error (model=%s): %s",
                model,
                exc,
            )
            return False

        if attempt < max_probes:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, backoff_max_s)

    logger.warning(
        "Contextualization probe exhausted after %d attempts (model=%s)",
        max_probes,
        model,
    )
    return False


async def contextualize_chunks(
    chunks: list[Chunk],
    source: str,
    model: str,
    *,
    timeout_s: float = 30.0,
    max_concurrency: int = 32,
    client_timeout_s: float = 60.0,
    probe_timeout_s: float = 8.0,
    probe_backoff_initial_s: float = 5.0,
    probe_backoff_max_s: float = 60.0,
    probe_max_probes: int = 10,
    global_gate: FifoCapacityGate | None = None,
) -> list[str]:
    """Generate context prefixes for chunks via LLM.

    Each context is a 50-100 token disambiguation prefix (per Anthropic's
    contextual retrieval findings). Neighboring chunk excerpts are included
    to help the LLM resolve references and identify structural position.

    Before dispatching the full batch, one probe request with tight client
    timeout verifies the model is warm. On retryable failure the probe retries
    with exponential backoff. This prevents all concurrent workers from entering
    a cold-load window simultaneously (stampede prevention).

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
        probe_timeout_s: Client timeout for the probe request (tight, used only
            to test model availability before committing the full batch).
        probe_backoff_initial_s: Initial wait (s) after a probe retryable failure.
        probe_backoff_max_s: Maximum per-retry wait (s) for probe backoff.
        probe_max_probes: Max probe attempts. 0 = disable probe-first pattern.
        global_gate: Optional cross-file capacity gate. When provided, each
            worker acquires a slot before calling the LLM, bounding total
            in-flight contextualization requests across all concurrent files.

    Returns:
        List of context strings (one per chunk). Empty string on per-chunk failure.
    """
    if not chunks:
        return []

    # Probe-first: verify model is reachable before committing the full batch.
    # Uses a tight timeout so a cold-loading model is detected quickly.
    if probe_max_probes > 0:
        model_ready = await _probe_model(
            model,
            timeout_s=probe_timeout_s,
            inference_timeout_s=timeout_s,
            backoff_initial_s=probe_backoff_initial_s,
            backoff_max_s=probe_backoff_max_s,
            max_probes=probe_max_probes,
        )
        if not model_ready:
            logger.warning(
                "Skipping contextualization for %s: probe failed after %d attempts "
                "(model=%s)",
                source,
                probe_max_probes,
                model,
            )
            return [""] * len(chunks)

    results: list[str] = [""] * len(chunks)
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
                except TimeoutError:
                    logger.warning(
                        "Contextualization gate timeout for chunk %d of %s",
                        idx,
                        source,
                    )
                except Exception:
                    logger.warning(
                        "Contextualization failed for chunk %d of %s", idx, source
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
    return results


# Timeout is supplied per call so config can shrink the queue wait budget
# without rebuilding the shared client.
_CLIENT = httpx.AsyncClient(timeout=None)

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
        f"{STARGATE_URL}/v1/chat/completions",
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
