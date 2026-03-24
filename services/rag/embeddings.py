from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import TYPE_CHECKING

import httpx

from services.rag.events.query import rag_embedding_query_success
from services.rag.extraction_model_tracker import ModelState, ModelStateTracker

if TYPE_CHECKING:
    from universal_event_bus import EventBus

GATEWAY_URL = "http://localhost:9999"

_client = httpx.AsyncClient(timeout=60.0)
logger = logging.getLogger(__name__)

_embed_model: str = ""
_probe_payload: dict[str, str | list[str]] = {}
_event_bus: EventBus | None = None
_tracker: ModelStateTracker | None = None

_CONTEXT_SUFFIX_RE = re.compile(r"-(\d+)(?:-(?:cpu|hybrid))?$")


def _extract_context_suffix(model_id: str) -> int | None:
    """Parse trailing context-size suffix from a synthetic model ID."""
    m = _CONTEXT_SUFFIX_RE.search(model_id)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        logger.warning(
            "Failed to parse context suffix as integer from model_id: %s", model_id
        )
        return None


def configure(model_id: str) -> None:
    """Set the embedding model ID from config. Call once at startup before any embed calls.

    Must be called before any embed_chunks/embed_query call; the module starts
    with an empty model ID and will raise on unconfigured use.
    """
    global _embed_model, _probe_payload
    if not model_id or not model_id.strip():
        raise ValueError(f"configure() received blank model_id: {model_id!r}")
    _embed_model = model_id
    _probe_payload = {"model": _embed_model, "input": ["probe"]}

    ctx = _extract_context_suffix(_embed_model)
    if ctx is not None:
        logger.info(
            "Embedding model configured: %s (context=%d). "
            "Verify this matches activated_gpu_contexts in the catalog entry.",
            _embed_model,
            ctx,
        )
    else:
        logger.info("Embedding model configured: %s", _embed_model)


async def start_tracker() -> None:
    """Start an event-driven model state tracker for the configured embedding model.

    Must be called after configure(). Subscribes to model.loaded / model.unloaded
    events from the Event Service so require_healthy() can gate on model state
    without polling. Safe to call multiple times; re-start stops the old tracker.
    """
    global _tracker
    if not _embed_model:
        logger.warning("start_tracker() called before configure() — skipping")
        return
    if _tracker is not None:
        await _tracker.stop()
    _tracker = ModelStateTracker(model_id=_embed_model)
    await _tracker.start()
    logger.info("Embedding model tracker started for '%s'", _embed_model)


async def stop_tracker() -> None:
    """Stop the embedding model tracker. Called during RAG shutdown."""
    global _tracker
    if _tracker is not None:
        await _tracker.stop()
        _tracker = None


def set_event_bus(bus: EventBus) -> None:
    """Inject the shared EventBus for emitting embedding telemetry signals.

    Must be called once at startup after configure(). Re-registering the same
    bus instance is a no-op; re-registering a different bus raises RuntimeError
    to prevent silent bus replacement.
    """
    global _event_bus
    if _event_bus is not None and _event_bus is not bus:
        raise RuntimeError(
            "Embedding event bus already initialised with a different instance"
        )
    _event_bus = bus


def _require_configured() -> None:
    """Raise if configure() has not been called."""
    if not _embed_model:
        raise RuntimeError(
            "Embedding module not configured — call configure(model_id) at startup"
        )


def get_model_id() -> str:
    """Return the currently configured embedding model ID."""
    _require_configured()
    return _embed_model


async def close() -> None:
    """Close the shared HTTP client during service shutdown."""
    await _client.aclose()


_PROBE_INTERVAL_S = 2.0
_PROBE_TIMEOUT_S = 120.0


async def wait_until_healthy(
    timeout_s: float = _PROBE_TIMEOUT_S,
    interval_s: float = _PROBE_INTERVAL_S,
) -> None:
    """Block until the embedding endpoint accepts requests, and cache the
    embedding dimension from the probe response for zero-vector fallbacks.

    ∀ t < timeout_s: retries on connection/HTTP errors (Stargate not yet ready).
    If the configured model is not activated, resolves a compatible sibling
    with context >= configured context from the same model family.
    Raises TimeoutError if endpoint is still unhealthy after timeout_s seconds.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    attempt = 0
    while True:
        attempt += 1
        try:
            response = await _client.post(
                f"{GATEWAY_URL}/v1/embeddings",
                json=_probe_payload,
                timeout=5.0,
            )
            response.raise_for_status()
            data = response.json().get("data", [])
            if data:
                _cache_embed_dim([item["embedding"] for item in data])
                logger.info(
                    "Embedding endpoint healthy after %d attempt(s) (dim=%s, model=%s)",
                    attempt,
                    _embed_dim,
                    _embed_model,
                )
            else:
                logger.info(
                    "Embedding endpoint healthy after %d attempt(s) (model=%s)",
                    attempt,
                    _embed_model,
                )
            return
        except Exception as exc:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"Embedding endpoint not healthy after {timeout_s}s"
                ) from exc
            logger.debug(
                "Embedding probe attempt %d failed (%s); retrying in %.1fs (%.0fs left)",
                attempt,
                exc,
                interval_s,
                remaining,
            )
            await asyncio.sleep(min(interval_s, remaining))


async def require_healthy(timeout_s: float = 120.0) -> None:
    """Gate indexing on the embedding model being loaded.

    Event-driven: checks the ModelStateTracker gate (asyncio.Event) set by
    model.loaded / model.unloaded events from the Event Service. No HTTP probe
    is issued, so a busy model queue never causes false "unreachable" failures.

    State semantics:
    - LOADED: pass through immediately
    - UNKNOWN: pass through (startup wait_until_healthy already confirmed health)
    - UNLOADED: wait for model.loaded event up to timeout_s

    Falls back to a brief HTTP probe if the tracker has not been started yet
    (e.g. during tests or early startup before start_tracker() runs).
    """
    if _tracker is not None:
        if _tracker.state != ModelState.UNLOADED:
            return
        # Model was explicitly unloaded — wait for reload
        logger.info(
            "Embedding model '%s' is UNLOADED — waiting up to %.0fs for reload",
            _embed_model,
            timeout_s,
        )
        ok = await _tracker.wait_until_loaded(timeout_s)
        if not ok:
            raise RuntimeError(
                f"Embedding model '{_embed_model}' did not reload within {timeout_s:.0f}s. "
                "Indexing disabled until embeddings are available."
            )
        return

    # Fallback: tracker not started — probe with retry (startup path or tests)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    attempt = 0
    last_exc: Exception | None = None
    while loop.time() < deadline:
        attempt += 1
        try:
            response = await _client.post(
                f"{GATEWAY_URL}/v1/embeddings",
                json=_probe_payload,
                timeout=15.0,
            )
            response.raise_for_status()
            data = response.json().get("data", [])
            if data:
                _cache_embed_dim([item["embedding"] for item in data])
            return
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Embedding endpoint returned {exc.response.status_code}: "
                f"{exc.response.text!r}. Indexing disabled until embeddings are available."
            ) from exc
        except httpx.RequestError as exc:
            last_exc = exc
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(2.0, remaining))
    raise RuntimeError(
        f"Embedding endpoint unreachable after {attempt} attempt(s): {last_exc}. "
        "Indexing disabled until embeddings are available."
    ) from last_exc


_SCOPE_INSTRUCTIONS: dict[str, str] = {
    "project": "Find relevant architecture documentation about this topic",
    "research": "Find relevant research papers and technical analysis about this topic",
    "prompting": "Find relevant prompt engineering techniques and patterns",
    "workflows": "Find relevant pipeline orchestration and agent coordination patterns",
    "llm_foundations": "Find relevant LLM reference material about this topic",
    "code_retrieval": "Find relevant code retrieval research about this topic",
    "both": "Find relevant documentation or research about this topic",
    "all": "Find relevant information about this topic",
}
_DEFAULT_INSTRUCTION = "Find relevant information about this topic"


def _is_instruction_aware_model(model_id: str) -> bool:
    """Detect whether the configured embedding model supports Instruct:/Query: format.

    Currently determined by presence of 'qwen3-embedding' in the model ID.
    """
    return "qwen3-embedding" in model_id.lower()


_EMBED_BATCH_SIZE = 64
# Conservative estimate: 3 chars ≈ 1 token for dense technical/research text.
# The qwen3 tokenizer produces more tokens per character than bge-m3 for English
# research content. Using 3 rather than 4 avoids the llama.cpp n_batch overflow
# seen with qwen3-embedding-8b-q8-0-4096 (n_batch=4096).
_CHARS_PER_TOKEN = 3

# n_ctx-aware ceiling: parse context size from model name (e.g. "...-4096").
# Falls back to 7000 for models without a parseable context suffix (e.g. bge-m3).
# Apply 0.85 headroom to keep aggregate batch tokens safely under llama.cpp's
# physical batch limit, which llama-server enforces as a hard ceiling.
_N_CTX_HEADROOM = 0.85
_FALLBACK_MAX_BATCH_TOKENS = 7000


def _max_batch_tokens_for_model(model_id: str) -> int:
    """Derive per-batch token cap from the model's context-size suffix.

    ∀ model_id ending in -<digits>: cap = digits * _N_CTX_HEADROOM.
    Fallback for models without a numeric suffix (e.g. bge-m3): _FALLBACK_MAX_BATCH_TOKENS.
    """
    ctx = _extract_context_suffix(model_id)
    if ctx is not None:
        return int(ctx * _N_CTX_HEADROOM)
    return _FALLBACK_MAX_BATCH_TOKENS


_EMBED_RETRY_ATTEMPTS = 3
_EMBED_RETRY_BACKOFF_S = 1.0

# Query-path retry tuning — shorter budget than index-path because
# each query is latency-sensitive (pipeline fan-out of 5 queries).
_QUERY_RETRY_ATTEMPTS = 4
_QUERY_RETRY_BASE_S = 0.25
_QUERY_RETRY_MAX_S = 3.0

_TRANSIENT_STATUS_CODES = frozenset({502, 503, 429})

# Cached from the first successful embedding response so zero-vector fallbacks
# match the model's output dimension. Set once, never reset.
_embed_dim: int | None = None


def _parse_embedding_rows(payload: dict[str, object]) -> list[list[float]]:
    """Extract embedding vectors from gateway response payload."""
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Embedding response missing list 'data' field")
    vectors: list[list[float]] = []
    for row in data:
        if not isinstance(row, dict):
            raise RuntimeError("Embedding response item is not an object")
        embedding = row.get("embedding")
        if not isinstance(embedding, list):
            raise RuntimeError("Embedding response item missing 'embedding' list")
        vectors.append(embedding)
    return vectors


def _cache_embed_dim(embeddings: list[list[float]]) -> None:
    """Cache embedding dimension from first successful embedding response.

    This value is intentionally write-once and then reused by zero-vector
    fallback paths for failed single-chunk embeddings.
    """
    global _embed_dim
    if _embed_dim is None and embeddings:
        _embed_dim = len(embeddings[0])


async def _handle_single_item_500(text: str, error_body: str) -> list[list[float]]:
    """Recover from a 500 on a single-item embedding batch.

    For oversized text (exceeds model's token limit): truncate and retry once,
    then fall back to zero vector if available.

    For within-limits text: the 500 indicates VRAM exhaustion or a transient
    model error — raise _TransientEmbeddingError so the caller can retry with
    backoff while the routing system evicts the competing model.
    """
    max_chars = _max_batch_tokens_for_model(_embed_model) * _CHARS_PER_TOKEN

    if len(text) > max_chars:
        truncated = text[:max_chars]
        logger.warning(
            "Truncating oversized text from %d to %d chars for embedding retry "
            "(model=%s)",
            len(text),
            max_chars,
            _embed_model,
        )
        try:
            response = await _client.post(
                f"{GATEWAY_URL}/v1/embeddings",
                json={"model": _embed_model, "input": [truncated]},
            )
            if response.status_code == 200:
                result = _parse_embedding_rows(response.json())
                _cache_embed_dim(result)
                return result
            logger.error(
                "Truncated text retry returned non-200 status %d "
                "(model=%s, truncated_len=%d)",
                response.status_code,
                _embed_model,
                max_chars,
            )
        except Exception as retry_exc:
            logger.error(
                "Truncated text still failed embedding (model=%s, truncated_len=%d): %s",
                _embed_model,
                max_chars,
                retry_exc,
            )

        if _embed_dim is not None:
            logger.warning(
                "Substituting zero vector (dim=%d) for failed embedding", _embed_dim
            )
            return [[0.0] * _embed_dim]

        raise RuntimeError(
            f"Single-item embedding failed and embedding dimension unknown "
            f"(model={_embed_model}, text_len={len(text)})"
        )

    logger.warning(
        "Single text within limits (len=%d <= %d chars) failed with 500 "
        "(model=%s) — transient error (VRAM pressure or model fault); retrying. Error: %s",
        len(text),
        max_chars,
        _embed_model,
        error_body,
    )
    raise _TransientEmbeddingError(
        f"Embedding 500 on within-limits text (model={_embed_model}, "
        f"text_len={len(text)})"
    )


def _fallback_to_zero_vector(text_len: int) -> list[list[float]] | None:
    """Return a zero vector for a chunk that exhausted all embedding retry attempts.

    Logs the substitution at WARNING level and emits rag.embedding.chunk.fallback
    when the event bus is configured. Returns None if the embedding dimension has
    not yet been cached (dim unknown), forcing callers to raise instead of
    silently producing an invalid zero-length vector.
    """
    from services.rag.events.indexing import rag_embedding_chunk_fallback

    if _embed_dim is None:
        return None
    logger.warning(
        "Single-item embedding failed after %d attempts (model=%s, text_len=%d) — "
        "content-specific fault; substituting zero vector (dim=%d)",
        _EMBED_RETRY_ATTEMPTS,
        _embed_model,
        text_len,
        _embed_dim,
    )
    if _event_bus is not None:
        _event_bus.publish_async_nowait(
            rag_embedding_chunk_fallback(
                model=_embed_model,
                text_len=text_len,
                dim=_embed_dim,
            )
        )
    return [[0.0] * _embed_dim]


async def _post_embeddings(batch: list[str]) -> list[list[float]]:
    """POST a single batch to the embedding endpoint with retry and fallback.

    Retry policy: up to _EMBED_RETRY_ATTEMPTS attempts with jittered exponential
    backoff on _TransientEmbeddingError, 502/503/429, and connection/timeout errors.
    On 500 with a multi-item batch: splits in half and recurses. The Gateway wraps
    llama-server overflow errors as "Internal embedding error" (raw overflow body is
    only in container logs), so overflow cannot be distinguished from other 500s —
    binary splitting is the only safe recovery for multi-item batches.
    Single-item 500s: delegate to _handle_single_item_500 (truncate + retry once,
    then _TransientEmbeddingError). After all retries exhausted for a single-item
    batch: _fallback_to_zero_vector (emits rag.embedding.chunk.fallback if bus set).
    """
    last_exc: Exception | None = None
    for attempt in range(_EMBED_RETRY_ATTEMPTS):
        try:
            response = await _client.post(
                f"{GATEWAY_URL}/v1/embeddings",
                json={"model": _embed_model, "input": batch},
            )
            if response.status_code == 500:
                body = response.text
                if len(batch) == 1:
                    return await _handle_single_item_500(batch[0], body)
                mid = len(batch) // 2
                logger.warning(
                    "Embedding 500 on batch of %d texts (model=%s); "
                    "splitting at midpoint %d for recovery. Error: %s",
                    len(batch),
                    _embed_model,
                    mid,
                    body,
                )
                left = await _post_embeddings(batch[:mid])
                right = await _post_embeddings(batch[mid:])
                return left + right
            response.raise_for_status()
            result = _parse_embedding_rows(response.json())
            _cache_embed_dim(result)
            return result
        except _TransientEmbeddingError as exc:
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in _TRANSIENT_STATUS_CODES:
                raise
            last_exc = exc
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_exc = exc
        base_delay = _EMBED_RETRY_BACKOFF_S * (2**attempt)
        delay = base_delay * random.uniform(0.75, 1.25)
        logger.warning(
            "Embedding request failed (attempt %d/%d, %s); retrying in %.1fs",
            attempt + 1,
            _EMBED_RETRY_ATTEMPTS,
            type(last_exc).__name__,
            delay,
        )
        await asyncio.sleep(delay)
    # A single-item batch that fails every attempt is a content-specific failure,
    # not transient VRAM pressure (which clears between retries).
    if isinstance(last_exc, _TransientEmbeddingError) and len(batch) == 1:
        fallback = _fallback_to_zero_vector(len(batch[0]))
        if fallback is not None:
            return fallback
        logger.error(
            "Single-item embedding failed after %d attempts (model=%s, "
            "text_len=%d) — embedding dimension unknown, cannot produce "
            "zero-vector fallback",
            _EMBED_RETRY_ATTEMPTS,
            _embed_model,
            len(batch[0]),
        )
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Embedding request failed without capturing an exception")


async def embed_chunks(texts: list[str]) -> list[list[float]]:
    """Embed raw texts for indexing.

    Splits into sub-batches bounded by both count and estimated token total.
    ∀ batch: len ≤ _EMBED_BATCH_SIZE ∧ Σ(tokens) ≤ max_batch_tokens.
    Prevents llama.cpp n_batch overflow when the aggregate input token count
    exceeds the model's physical batch size.
    """
    _require_configured()
    max_batch_tokens = _max_batch_tokens_for_model(_embed_model)
    all_embeddings: list[list[float]] = []
    batch: list[str] = []
    batch_tokens = 0

    for text in texts:
        token_estimate = max(1, len(text) // _CHARS_PER_TOKEN)
        if token_estimate > max_batch_tokens:
            if batch:
                all_embeddings.extend(await _post_embeddings(batch))
                batch = []
                batch_tokens = 0
            logger.warning(
                "Single text estimate exceeds batch cap (tokens=%d > cap=%d, model=%s); "
                "sending as single-item batch for truncation/fallback handling",
                token_estimate,
                max_batch_tokens,
                _embed_model,
            )
            all_embeddings.extend(await _post_embeddings([text]))
            continue
        if batch and (
            len(batch) >= _EMBED_BATCH_SIZE
            or batch_tokens + token_estimate > max_batch_tokens
        ):
            all_embeddings.extend(await _post_embeddings(batch))
            batch = []
            batch_tokens = 0
        batch.append(text)
        batch_tokens += token_estimate

    if batch:
        all_embeddings.extend(await _post_embeddings(batch))

    return all_embeddings


class _TransientEmbeddingError(Exception):
    """Raised for potentially transient embedding backend failures.

    This exception represents retryable conditions such as VRAM pressure or
    transient model faults. Callers should retry with backoff. If a single-item
    batch still fails after all retries, the failure is treated as content-
    specific and _post_embeddings may substitute a zero vector fallback.
    """


class EmbeddingTransientError(Exception):
    """Raised when embed_query retries are exhausted on transient failures."""

    def __init__(
        self,
        message: str,
        *,
        model_id: str,
        attempts: int,
        last_status: int | None,
    ) -> None:
        super().__init__(message)
        self.model_id = model_id
        self.attempts = attempts
        self.last_status = last_status


def _is_transient_status(status_code: int) -> bool:
    return status_code in _TRANSIENT_STATUS_CODES


def _format_query_text(text: str, scope: str | list[str] | None = None) -> str:
    """Apply instruction prefix for instruction-aware embedding models."""
    if isinstance(scope, list):
        effective_scope = scope[0] if scope else None
    else:
        effective_scope = scope
    if _is_instruction_aware_model(_embed_model):
        instruction = _SCOPE_INSTRUCTIONS.get(
            effective_scope or "", _DEFAULT_INSTRUCTION
        )
        return f"Instruct: {instruction}\nQuery: {text}"
    return f"search_query: {text}"


async def embed_queries_batch(
    texts: list[str],
    scope: str | list[str] | None = None,
) -> list[list[float]]:
    """Embed multiple search queries in a single batch forward pass.

    All texts are formatted with instruction prefixes (same as embed_query)
    and sent to the Gateway as one ``/v1/embeddings`` call. The GPU processes
    them in a single forward pass, eliminating per-query embedding latency.

    Uses the same retry and overflow recovery as ``embed_chunks`` via
    ``_post_embeddings``.

    Raises EmbeddingTransientError on total failure.
    """
    _require_configured()
    if not texts:
        return []
    formatted = [_format_query_text(t, scope) for t in texts]
    try:
        embeddings = await _post_embeddings(formatted)
    except Exception:
        logger.error(
            "embed_queries_batch failed for %d texts (model=%s)",
            len(texts),
            _embed_model,
            exc_info=True,
        )
        raise EmbeddingTransientError(
            f"Batch query embedding failed for {len(texts)} texts (model={_embed_model})",
            model_id=_embed_model,
            attempts=_EMBED_RETRY_ATTEMPTS,
            last_status=None,
        )
    if _event_bus is not None:
        _event_bus.publish_async_nowait(
            rag_embedding_query_success(
                model_id=_embed_model,
                query_len=sum(len(t) for t in texts),
                scope=scope,
            )
        )
    return embeddings


async def embed_query(text: str, scope: str | list[str] | None = None) -> list[float]:
    """Embed a search query with bounded jittered backoff on transient errors.

    Retry policy: exponential backoff with ±25% jitter, capped at _QUERY_RETRY_MAX_S.
    Retries on 502/503/429 and connection/timeout errors only.
    Raises EmbeddingTransientError when retries are exhausted so callers can
    distinguish transient unavailability from permanent errors.
    """
    _require_configured()
    if isinstance(scope, list):
        if scope and len(scope) > 1:
            logger.warning(
                "embed_query received multiple scopes; using first only: %s",
                scope[0],
            )
        effective_scope = scope[0] if scope else None
    else:
        effective_scope = scope
    formatted = _format_query_text(text, scope=effective_scope)

    last_exc: Exception | None = None
    last_status: int | None = None

    for attempt in range(1, _QUERY_RETRY_ATTEMPTS + 1):
        try:
            response = await _client.post(
                f"{GATEWAY_URL}/v1/embeddings",
                json={"model": _embed_model, "input": [formatted]},
            )
            if _is_transient_status(response.status_code):
                last_status = response.status_code
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
            else:
                response.raise_for_status()
                data = response.json()
                embedding = data["data"][0]["embedding"]
                if _event_bus is not None:
                    _event_bus.publish_async_nowait(
                        rag_embedding_query_success(
                            model_id=_embed_model,
                            query_len=len(text),
                            scope=scope,
                        )
                    )
                return embedding
        except httpx.HTTPStatusError as exc:
            if not _is_transient_status(exc.response.status_code):
                raise
            last_status = exc.response.status_code
            last_exc = exc
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_exc = exc

        if attempt < _QUERY_RETRY_ATTEMPTS:
            base_delay = _QUERY_RETRY_BASE_S * (2 ** (attempt - 1))
            delay = min(base_delay, _QUERY_RETRY_MAX_S) * random.uniform(0.75, 1.25)
            logger.warning(
                "embed_query failed (attempt %d/%d, %s); retrying in %.2fs (model=%s)",
                attempt,
                _QUERY_RETRY_ATTEMPTS,
                type(last_exc).__name__,
                delay,
                _embed_model,
            )
            await asyncio.sleep(delay)

    logger.error(
        "embed_query retries exhausted (%d attempts, last_status=%s, model=%s)",
        _QUERY_RETRY_ATTEMPTS,
        last_status,
        _embed_model,
    )
    if _event_bus is not None:
        from services.rag.events.query import rag_embedding_query_failed

        _event_bus.publish_async_nowait(
            rag_embedding_query_failed(
                model_id=_embed_model,
                attempts=_QUERY_RETRY_ATTEMPTS,
                last_status=last_status,
                query_len=len(text),
                scope=scope,
            )
        )
    raise EmbeddingTransientError(
        f"Embedding query failed after {_QUERY_RETRY_ATTEMPTS} attempts "
        f"(model={_embed_model}, last_status={last_status})",
        model_id=_embed_model,
        attempts=_QUERY_RETRY_ATTEMPTS,
        last_status=last_status,
    )
