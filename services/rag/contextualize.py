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
  - Stampede protection has two layers, each strictly optimization:
      1. ``max_concurrency`` bounds in-flight workers per file.
      2. ``admission_gate`` (AdmissionGate) pauses workers while Stargate's
         admission is closed, the model is mid-cold-load, or a federated
         gateway is degraded (subscribed to Stargate's coordination signals
         — see admission_gate.py).
    Neither is a correctness gate. Per the stargate-model-lifecycle
    invariant, callers MUST proceed when coordination signals are
    unavailable; the per-chunk X-Request-Timeout enforced server-side is
    the correctness backstop.
  - X-Request-Timeout header enforces per-chunk inference deadline from the
    moment Stargate acquires the slot, not from enqueue time.
  - Per-chunk failures are reported to the caller for partial-tolerant
    indexing; failed chunks are embedded prefix-free and remain cache misses.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
from dataclasses import dataclass
from math import ceil
from time import monotonic
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_event_bus.events.debug import emit_debug_event

from services.rag.chunkers import Chunk

if TYPE_CHECKING:
    from services.rag.admission_gate import AdmissionGate

logger = logging.getLogger(__name__)


class ContextualizationDiagnosticsSink(Protocol):
    async def __call__(
        self,
        event: str,
        *,
        chunk_index: int,
        request_id: str,
        duration_seconds: float | None = None,
        error: str | None = None,
        output_chars: int | None = None,
    ) -> None: ...


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


@dataclass(slots=True, kw_only=True, frozen=True)
class ContextualizationResult:
    """Outcome of contextualizing one file's chunks.

    contexts: list of length len(chunks); "" at positions that failed.
    failed_indices: positions that failed (parallel to contexts).
    first_failure_repr: repr() of the first failure exception, for logging.
    failure_reprs: chunk_index → repr(exc)[:200] for every failed chunk.
        Used by callers to emit per-chunk events without re-deriving the
        exception from failed_indices.
    request_ids: chunk_index → Stargate request ID, for event correlation.
    abandoned_indices: chunk positions abandoned by the tail-idle policy.
    tail_idle_seconds: observed no-progress interval that triggered abandonment.

    Empty-prefix invariant (see contextualize_cache.py): "" entries flow
    into embeddings prefix-free and are filtered out before cache write.
    """

    contexts: list[str]
    failed_indices: list[int]
    first_failure_repr: str | None
    failure_reprs: dict[int, str]
    request_ids: dict[int, str]
    abandoned_indices: list[int]
    tail_idle_seconds: float | None

    @property
    def failed_count(self) -> int:
        return len(self.failed_indices)

    @property
    def successful_count(self) -> int:
        return len(self.contexts) - self.failed_count


async def contextualize_chunks(
    chunks: list[Chunk],
    source: str,
    model: str,
    *,
    timeout_s: float = 300.0,
    max_concurrency: int = 32,
    client_timeout_s: float = 600.0,
    tail_idle_timeout_s: float = 45.0,
    tail_min_success_ratio: float = 0.5,
    chunk_indices: list[int] | None = None,
    diagnostics_sink: ContextualizationDiagnosticsSink | None = None,
    admission_gate: AdmissionGate | None = None,
) -> ContextualizationResult:
    """Generate context prefixes for chunks via LLM.

    Each context is a 50-100 token disambiguation prefix (per Anthropic's
    contextual retrieval findings). Neighboring chunk excerpts are included
    to help the LLM resolve references and identify structural position.

    Workers send requests directly to Stargate; Stargate handles model loading
    transparently per the stargate-model-lifecycle invariant. Stampede
    protection comes from ``max_concurrency`` (per-file) and the optional
    ``admission_gate`` (pauses workers while Stargate's admission is closed
    or the model is mid-cold-load, or while a federated gateway is degraded).

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
        tail_idle_timeout_s: After enough chunks have settled, stop waiting for
            stragglers if no chunk makes progress for this many seconds.
        tail_min_success_ratio: Fraction of chunks that must settle before the
            tail-idle policy may abandon stragglers.
        chunk_indices: Optional original source chunk indices. Used when
            contextualizing cache misses only, so diagnostics refer to the
            source chunk positions rather than the miss-list positions.
        diagnostics_sink: Optional async callback for per-chunk diagnostics.
        admission_gate: Optional event-driven admission gate. When provided,
            each worker awaits the configured model's admission gate before
            submitting. Coordination only — timeout returns False and the
            worker proceeds, with the per-chunk X-Request-Timeout (Phase 1)
            backstopping correctness.

    Returns:
        ContextualizationResult: list of contexts (one per chunk; "" at
        positions that failed) plus structured failure metadata. Failed
        chunks are embedded prefix-free; the file is still indexable.

    No exceptions are raised for per-chunk failures — they are reported
    via the returned ContextualizationResult. Infrastructure errors that
    prevent the worker pool from running at all (e.g. asyncio.CancelledError
    from shutdown) propagate normally.
    """
    if not chunks:
        return ContextualizationResult(
            contexts=[],
            failed_indices=[],
            first_failure_repr=None,
            failure_reprs={},
            request_ids={},
            abandoned_indices=[],
            tail_idle_seconds=None,
        )

    results: list[str] = [""] * len(chunks)
    failures: list[tuple[int, BaseException]] = []
    failure_reprs: dict[int, str] = {}
    request_ids: dict[int, str] = {}
    abandoned_indices: list[int] = []
    tail_idle_seconds: float | None = None
    started_indices: set[int] = set()
    worker_count = max(1, min(max_concurrency, len(chunks)))
    external_indices = chunk_indices or list(range(len(chunks)))
    if len(external_indices) != len(chunks):
        raise ValueError("chunk_indices length must match chunks length")

    semaphore = asyncio.Semaphore(worker_count)

    async def _run_chunk(idx: int) -> None:
        request_id = str(uuid4())
        external_idx = external_indices[idx]
        request_ids[external_idx] = request_id
        start = monotonic()
        async with semaphore:
            started_indices.add(idx)
            if diagnostics_sink is not None:
                await diagnostics_sink(
                    "started",
                    chunk_index=external_idx,
                    request_id=request_id,
                )
            try:
                if admission_gate is not None:
                    await admission_gate.wait_for_admission(
                        model, timeout=client_timeout_s
                    )
                user_msg = _build_chunk_context(idx, chunks, source)
                context = await _call_llm(
                    user_msg,
                    model,
                    timeout_s,
                    client_timeout_s=client_timeout_s,
                    request_id=request_id,
                )
                if not context:
                    raise ValueError("model returned empty context for chunk")
                results[idx] = context
                if diagnostics_sink is not None:
                    await diagnostics_sink(
                        "completed",
                        chunk_index=external_idx,
                        request_id=request_id,
                        duration_seconds=monotonic() - start,
                        output_chars=len(context),
                    )
            except Exception as exc:
                failures.append((external_idx, exc))
                failure_reprs[external_idx] = repr(exc)[:200]
                logger.warning(
                    "Contextualization failed for chunk %d of %s: %r",
                    external_idx,
                    source,
                    exc,
                )
                if diagnostics_sink is not None:
                    await diagnostics_sink(
                        "failed",
                        chunk_index=external_idx,
                        request_id=request_id,
                        duration_seconds=monotonic() - start,
                        error=repr(exc)[:200],
                    )

    tasks: dict[asyncio.Task[None], int] = {
        asyncio.create_task(_run_chunk(idx), name=f"contextualize-chunk-{idx}"): idx
        for idx in range(len(chunks))
    }
    pending: set[asyncio.Task[None]] = set(tasks)
    last_progress = monotonic()
    tail_min_successes = max(
        1,
        min(len(chunks), ceil(len(chunks) * tail_min_success_ratio)),
    )

    while pending:
        done, pending = await asyncio.wait(
            pending,
            timeout=tail_idle_timeout_s,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if done:
            last_progress = monotonic()
            for task in done:
                await task
            continue
        if sum(1 for context in results if context) < tail_min_successes:
            continue

        idle_s = monotonic() - last_progress
        tail_idle_seconds = idle_s
        active_tasks = [task for task in pending if tasks[task] in started_indices]
        if not active_tasks:
            continue
        for task in active_tasks:
            task.cancel()
        abandoned_tasks = active_tasks
        await asyncio.gather(*abandoned_tasks, return_exceptions=True)
        for task in abandoned_tasks:
            idx = tasks[task]
            external_idx = external_indices[idx]
            request_id = request_ids.get(external_idx, "")
            abandoned_indices.append(external_idx)
            cancel_requested = await _cancel_llm_request(request_id, model)
            msg = (
                "ContextualizationTailAbandoned("
                f"request_id={request_id}, idle_s={idle_s:.3f}, "
                f"tail_idle_timeout_s={tail_idle_timeout_s:.3f}, "
                f"cancel_requested={cancel_requested})"
            )
            failure_reprs[external_idx] = msg
            if diagnostics_sink is not None:
                await diagnostics_sink(
                    "abandoned",
                    chunk_index=external_idx,
                    request_id=request_id,
                    duration_seconds=idle_s,
                    error=msg,
                )
        pending.difference_update(abandoned_tasks)
        last_progress = monotonic()

    successful = sum(1 for r in results if r)
    if failures or abandoned_indices:
        logger.warning(
            "Partial contextualization for %s: %d/%d chunks succeeded "
            "(model=%s, concurrency=%d, first failure: chunk %d: %r)",
            source,
            successful,
            len(chunks),
            model,
            worker_count,
            failures[0][0] if failures else abandoned_indices[0],
            failures[0][1] if failures else failure_reprs[abandoned_indices[0]],
        )
    else:
        logger.info(
            "Contextualized %d/%d chunks for %s (model=%s, concurrency=%d)",
            successful,
            len(chunks),
            source,
            model,
            worker_count,
        )
    failed_indices = sorted(idx for idx, _exc in failures)
    first_repr = repr(failures[0][1]) if failures else None
    failed_indices = sorted({*failed_indices, *abandoned_indices})
    if first_repr is None and abandoned_indices:
        first_repr = failure_reprs[abandoned_indices[0]]
    return ContextualizationResult(
        contexts=results,
        failed_indices=failed_indices,
        first_failure_repr=first_repr,
        failure_reprs=failure_reprs,
        request_ids=request_ids,
        abandoned_indices=sorted(abandoned_indices),
        tail_idle_seconds=tail_idle_seconds,
    )


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
    request_id: str,
) -> str:
    """Call the contextualization LLM for a single chunk.

    timeout_s is passed as X-Request-Timeout so Stargate starts the clock when
    inference begins, not when the request enters the queue. The httpx client
    timeout is a wide ceiling covering queue wait + inference time.

    Returns the context string, or empty string on failure.
    """
    response = await _CLIENT.post(
        "/v1/chat/completions",
        headers={
            "X-Internal-Request-ID": request_id,
            "X-Request-Timeout": str(timeout_s),
        },
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


async def _cancel_llm_request(request_id: str, model: str) -> bool:
    """Best-effort cancellation for abandoned Stargate requests."""
    if not request_id:
        return False
    try:
        response = await _CLIENT.post(
            "/api/v1/pipeline/cancel",
            json={"request_id": request_id, "model_id": model},
            timeout=5.0,
        )
        if response.status_code >= 400:
            return False
        data = response.json()
        return bool(data.get("cancelled"))
    except Exception as exc:
        await emit_debug_event(
            "rag.debug.contextualization",
            {
                "step": "cancel_failed",
                "request_id": request_id,
                "model": model,
                "error": repr(exc)[:200],
            },
            source="rag.contextualize",
        )
        return False
