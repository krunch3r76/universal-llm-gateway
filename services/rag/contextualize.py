"""LLM-based contextual embedding enrichment for RAG chunks.

Generates a short context prefix per chunk that disambiguates its content
within the parent document. The prefix is prepended to the chunk text
*only* for embedding — stored document text remains the original.

This follows the "contextual retrieval" pattern: chunks that share
overlapping vocabulary (e.g. "knowledge graph" appearing in both PKB
schema papers and enterprise KG surveys) get distinct embedding vectors
because their context prefixes anchor them to different documents.

Architecture:
  - Sends each chunk to a small LLM (e.g. qwen3-5-9b) via Stargate
  - Stargate queues requests based on the model's parallel_slots —
    no application-level concurrency control needed
  - Returns context strings; empty string on per-chunk failure (graceful)
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from services.rag.chunkers import Chunk

logger = logging.getLogger(__name__)

STARGATE_URL = "http://localhost:9999"

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


async def contextualize_chunks(
    chunks: list[Chunk],
    source: str,
    model: str,
    *,
    timeout_s: float = 30.0,
) -> list[str]:
    """Generate context prefixes for chunks via LLM.

    Each context is a 50-100 token disambiguation prefix (per Anthropic's
    contextual retrieval findings). Neighboring chunk excerpts are included
    to help the LLM resolve references and identify structural position.

    Concurrency is bounded by Stargate's slot-based request queuing — the
    model's parallel_slots config determines how many requests run on the
    GPU simultaneously; excess requests wait in Stargate's capacity queue.

    Args:
        chunks: Chunks to contextualize.
        source: Source file path (included in the prompt for document identity).
        model: Model ID for context generation (e.g. qwen3-5-9b-q8-0-262144).
        timeout_s: Per-chunk timeout in seconds.

    Returns:
        List of context strings (one per chunk). Empty string on per-chunk failure.
    """
    if not chunks:
        return []

    results: list[str] = [""] * len(chunks)

    async def _generate_one(idx: int) -> None:
        try:
            user_msg = _build_chunk_context(idx, chunks, source)
            context = await _call_llm(user_msg, model, timeout_s)
            results[idx] = context
        except Exception:
            logger.warning(
                "Contextualization failed for chunk %d of %s", idx, source
            )

    tasks = [asyncio.create_task(_generate_one(i)) for i in range(len(chunks))]
    await asyncio.gather(*tasks)

    successful = sum(1 for r in results if r)
    logger.info(
        "Contextualized %d/%d chunks for %s (model=%s)",
        successful,
        len(chunks),
        source,
        model,
    )
    return results


_CLIENT = httpx.AsyncClient(timeout=60.0)

# Anthropic research: 50-100 token prefixes are the sweet spot for 1024-token chunks.
_MAX_CONTEXT_TOKENS = 150


async def _call_llm(
    user_msg: str,
    model: str,
    timeout_s: float,
) -> str:
    """Call the contextualization LLM for a single chunk.

    Returns the context string, or empty string on failure.
    """
    response = await _CLIENT.post(
        f"{STARGATE_URL}/v1/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": _CONTEXT_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": _MAX_CONTEXT_TOKENS,
            "temperature": 0.1,
        },
        timeout=timeout_s,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return content.strip() if isinstance(content, str) else ""
