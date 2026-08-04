"""LLM-based contextual embedding enrichment for RAG chunks.

Generates a short context prefix per chunk that disambiguates its content
within the parent document. The prefix is prepended to the chunk text
*only* for embedding — stored document text remains the original.

This follows the "contextual retrieval" pattern: chunks that share
overlapping vocabulary (e.g. "knowledge graph" appearing in both PKB
schema papers and enterprise KG surveys) get distinct embedding vectors
because their context prefixes anchor them to different documents.

Architecture:
  - contextualize_chunks() pre-builds user messages (with neighbor excerpts)
    then dispatches a single pipeline call to the rag-contextualize pipeline.
  - The pipeline's MapExecutor fans out over chunks with max_concurrency=32
    and min_success_threshold=0.5, handling per-chunk parallelism, timeouts,
    and partial-success accounting.
  - admission_gate (AdmissionGate) pauses the pipeline call while Stargate's
    admission is closed, the model is mid-cold-load, or a federated gateway
    is degraded. Per the stargate-model-lifecycle invariant, this is an
    optimization — the per-chunk pipeline timeout is the correctness backstop.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from transport_utils import DEFAULT_STARGATE_URL, make_async_client

from services.rag.chunkers import Chunk

if TYPE_CHECKING:
    from services.rag.admission_gate import AdmissionGate

logger = logging.getLogger(__name__)


_NEIGHBOR_CHARS = 800
_NEIGHBOR_DIGEST_SEP = "\x1e"


def compute_neighbor_digest(chunks: list[Chunk], idx: int) -> str:
    """Return SHA-256 over prev/next neighbor excerpts for G1 cache keying."""
    prev_excerpt = chunks[idx - 1].text[-_NEIGHBOR_CHARS:] if idx > 0 else ""
    next_excerpt = (
        chunks[idx + 1].text[:_NEIGHBOR_CHARS] if idx < len(chunks) - 1 else ""
    )
    material = prev_excerpt + _NEIGHBOR_DIGEST_SEP + next_excerpt
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ContextualizationPhaseError(RuntimeError):
    """All cache-miss chunks failed contextualization at the indexing phase boundary."""

    def __init__(
        self,
        message: str,
        *,
        first_failure_exc: BaseException | None = None,
        failure_category: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.first_failure_exc = first_failure_exc
        self.failure_category = failure_category
        self.failure_reason = failure_reason
        if first_failure_exc is not None:
            self.__cause__ = first_failure_exc


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
    first_failure_repr: repr() of the first failure, for logging.
    failure_reprs: chunk_index → brief failure description for every failed chunk.
        Used by callers to emit per-chunk events without re-deriving the exception.
    request_ids: chunk_index → pipeline request ID. With pipeline dispatch the
        same pipeline execution ID is stored for every failed chunk (one call
        per file, not one call per chunk).
    abandoned_indices: always empty — the pipeline handles partial failure via
        min_success_threshold; there is no tail-idle abandonment concept.
    tail_idle_seconds: always None — no tail-idle policy at the caller level.

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
    first_failure_exc: BaseException | None = None
    failure_category: str | None = None
    failure_reason: str | None = None

    @property
    def failed_count(self) -> int:
        return len(self.failed_indices)

    @property
    def successful_count(self) -> int:
        return len(self.contexts) - self.failed_count


# TODO: Detect Persian/Arabic-script chunks and request Farsi context prefixes.
_CONTEXT_SYSTEM_PROMPT = (
    "You are a search indexing assistant. Given a chunk from a document "
    "and surrounding context, write a short succinct context (2-3 sentences, "
    "under 100 tokens) to situate this chunk within the overall document "
    "for the purposes of improving search retrieval. Focus on: which document "
    "this is from, what specific topic the chunk covers, and resolving any "
    "ambiguous references (pronouns, abbreviations, 'the method', etc.). "
    "Output ONLY the context sentences, nothing else."
)

_MAX_CONTEXT_TOKENS = 150

# Bump only when invalidation must cross a refactor that does NOT change the
# sources hashed below (e.g. contributor intent diverges from code change).
_CONTEXTUALIZE_ALGORITHM_VERSION = "2"  # v2: pipeline dispatch replaces per-chunk calls


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

_CLIENT = make_async_client(DEFAULT_STARGATE_URL, timeout=None)


async def contextualize_chunks(
    chunks: list[Chunk],
    source: str,
    model: str,
    *,
    pipeline: str = "rag-contextualize",
    client_timeout_s: float = 3600.0,
    chunk_indices: list[int] | None = None,
    admission_gate: AdmissionGate | None = None,
) -> ContextualizationResult:
    """Generate context prefixes for chunks via the rag-contextualize pipeline.

    Pre-builds user messages (with neighbor excerpts) for all chunks, then
    dispatches a single pipeline call. The pipeline's MapExecutor handles
    per-chunk parallelism and partial-success accounting.

    Args:
        chunks: Chunks to contextualize.
        source: Source file path (included in each prompt for document identity).
        model: Model ID for the admission gate check. The pipeline handles actual
            model selection via its models.yaml; this parameter is used only for
            AdmissionGate.wait_for_admission() before the pipeline call.
        pipeline: Pipeline virtual model ID (registered in Stargate).
        client_timeout_s: Outer HTTP timeout for the pipeline call, covering
            cold-model load plus all-chunk inference.
        chunk_indices: Optional original source chunk indices. Used when
            contextualizing cache misses only, so results map back to source
            chunk positions rather than the miss-list positions.
        admission_gate: Optional event-driven admission gate. When provided,
            the gate is checked once before the pipeline call. Coordination only
            — timeout returns False and the call proceeds, with the pipeline's
            per-chunk inference_timeout_seconds backstopping correctness.

    Returns:
        ContextualizationResult: list of contexts (one per chunk; "" at
        positions that failed) plus structured failure metadata. Failed
        chunks are embedded prefix-free; the file is still indexable.

    No exceptions are raised for per-chunk failures — they are reported
    via the returned ContextualizationResult. Infrastructure errors that
    prevent the pipeline call from running at all (e.g. asyncio.CancelledError
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
            first_failure_exc=None,
        )

    external_indices = chunk_indices or list(range(len(chunks)))
    if len(external_indices) != len(chunks):
        raise ValueError("chunk_indices length must match chunks length")

    if admission_gate is not None:
        await admission_gate.wait_for_admission(model, timeout=client_timeout_s)

    request_id = str(uuid4())
    chunk_payloads = [
        {"user_msg": _build_chunk_context(i, chunks, source)}
        for i in range(len(chunks))
    ]

    try:
        response = await _CLIENT.post(
            "/v1/chat/completions",
            headers={"X-Internal-Request-ID": request_id},
            json={
                "model": pipeline,
                "messages": [{"role": "user", "content": "contextualize"}],
                "pipeline_options": {"chunks": chunk_payloads, "source": source},
            },
            timeout=client_timeout_s,
        )
        response.raise_for_status()
    except Exception as exc:
        failure_repr = repr(exc)[:300]
        logger.warning(
            "Contextualization pipeline call failed for %s (%d chunks): %r",
            source,
            len(chunks),
            exc,
        )
        failed_indices = external_indices
        return ContextualizationResult(
            contexts=[""] * len(chunks),
            failed_indices=sorted(failed_indices),
            first_failure_repr=failure_repr,
            failure_reprs={idx: failure_repr for idx in external_indices},
            request_ids={idx: request_id for idx in external_indices},
            abandoned_indices=[],
            tail_idle_seconds=None,
            first_failure_exc=exc,
        )

    return _parse_pipeline_response(
        response.json(),
        external_indices=external_indices,
        request_id=request_id,
    )


def _parse_pipeline_response(
    body: object,
    *,
    external_indices: list[int],
    request_id: str,
) -> ContextualizationResult:
    """Parse a rag-contextualize pipeline JSON response into a ContextualizationResult.

    The pipeline uses output_format: json_array — the content field is a JSON
    array of per-chunk results. Each item is either {"context": "..."} or null
    for a failed iteration.
    """
    import json as _json

    n = len(external_indices)
    contexts = [""] * n
    failure_reprs: dict[int, str] = {}
    request_ids: dict[int, str] = {}

    try:
        if not isinstance(body, dict):
            raise ValueError(f"Unexpected response type: {type(body).__name__}")
        choices = body.get("choices") or []
        content = choices[0]["message"]["content"] if choices else "[]"
        items = _json.loads(content)
        if not isinstance(items, list):
            raise ValueError(f"Expected JSON array, got {type(items).__name__}")
    except Exception as exc:
        failure_repr = f"ResponseParseError: {exc!r}"[:300]
        logger.warning("Failed to parse contextualization pipeline response: %r", exc)
        return ContextualizationResult(
            contexts=contexts,
            failed_indices=sorted(external_indices),
            first_failure_repr=failure_repr,
            failure_reprs={idx: failure_repr for idx in external_indices},
            request_ids={idx: request_id for idx in external_indices},
            abandoned_indices=[],
            tail_idle_seconds=None,
            failure_category="permanent",
            failure_reason="response_parse_error",
        )

    failed_indices: list[int] = []
    first_failure: str | None = None
    first_failure_category: str | None = None
    first_failure_reason: str | None = None

    def _record_pipeline_failure(ext_idx: int, reason: str) -> None:
        nonlocal first_failure, first_failure_category, first_failure_reason
        failure_reprs[ext_idx] = reason
        request_ids[ext_idx] = request_id
        failed_indices.append(ext_idx)
        if first_failure is None:
            first_failure = reason
            first_failure_category = "permanent"
            first_failure_reason = reason

    for i, ext_idx in enumerate(external_indices):
        if i >= len(items):
            _record_pipeline_failure(ext_idx, "count_mismatch")
            continue

        item = items[i]
        if item is None:
            _record_pipeline_failure(ext_idx, "missing_iteration_output")
            continue

        if not isinstance(item, dict):
            _record_pipeline_failure(
                ext_idx, f"unexpected_item_type:{type(item).__name__}"
            )
            continue

        context = item.get("context", "")
        if not isinstance(context, str) or not context.strip():
            _record_pipeline_failure(ext_idx, "empty_context")
            continue

        contexts[i] = context.strip()

    if failures_count := len(failed_indices):
        logger.warning(
            "Partial contextualization: %d/%d chunks failed (pipeline=%s)",
            failures_count,
            n,
            "rag-contextualize",
        )

    return ContextualizationResult(
        contexts=contexts,
        failed_indices=sorted(failed_indices),
        first_failure_repr=first_failure,
        failure_reprs=failure_reprs,
        request_ids=request_ids,
        abandoned_indices=[],
        tail_idle_seconds=None,
        failure_category=first_failure_category,
        failure_reason=first_failure_reason,
    )
