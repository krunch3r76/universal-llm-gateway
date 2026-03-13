from __future__ import annotations

import asyncio
import logging
import random
import re

import httpx

GATEWAY_URL = "http://localhost:9999"

_client = httpx.AsyncClient(timeout=60.0)
logger = logging.getLogger(__name__)

_embed_model: str = "bge-m3-q8-0-8192-cpu"
_probe_payload: dict[str, object] = {"model": _embed_model, "input": ["probe"]}

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

    Validates the model ID is non-blank and logs the resolved context size
    so operators can detect 4096-vs-8192 mismatches at startup.
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


_PROBE_INTERVAL_S = 2.0
_PROBE_TIMEOUT_S = 120.0


async def wait_until_healthy(
    timeout_s: float = _PROBE_TIMEOUT_S,
    interval_s: float = _PROBE_INTERVAL_S,
) -> None:
    """Block until the embedding endpoint accepts requests.

    ∀ t < timeout_s: retries on connection/HTTP errors (Stargate not yet ready).
    Raises TimeoutError if endpoint is still unhealthy after timeout_s seconds.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
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
            logger.info("Embedding endpoint healthy after %d attempt(s)", attempt)
            return
        except Exception as exc:
            remaining = deadline - asyncio.get_event_loop().time()
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
    parts = model_id.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        try:
            return int(int(parts[1]) * _N_CTX_HEADROOM)
        except ValueError:
            logger.warning(
                "Failed to parse context suffix as integer from model_id: %s", model_id
            )
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


def _cache_embed_dim(embeddings: list[list[float]]) -> None:
    global _embed_dim
    if _embed_dim is None and embeddings:
        _embed_dim = len(embeddings[0])


async def _handle_single_item_500(text: str, error_body: str) -> list[list[float]]:
    """Recover from a 500 on a single-item embedding batch.

    Strategy: truncate to the model's safe character limit and retry once.
    If still failing, substitute a zero vector (neutral in cosine space)
    rather than aborting the entire file.
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
                result = [item["embedding"] for item in response.json()["data"]]
                _cache_embed_dim(result)
                return result
        except Exception as retry_exc:
            logger.error(
                "Truncated text still failed embedding (model=%s, truncated_len=%d): %s",
                _embed_model,
                max_chars,
                retry_exc,
            )
            # Fall through to zero-vector or raise below
        else:
            logger.error(
                "Truncated text still failed embedding (model=%s, truncated_len=%d)",
                _embed_model,
                max_chars,
            )
    else:
        logger.error(
            "Single text failed embedding (model=%s, len=%d chars); "
            "unrecoverable model error. Error: %s",
            _embed_model,
            len(text),
            error_body,
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


async def _post_embeddings(batch: list[str]) -> list[list[float]]:
    """POST a single batch to the embedding endpoint with retry on transient errors.

    Retries on connection failures and 503 (service temporarily unavailable).
    On 500 with a multi-item batch: splits in half and recurses. The Gateway wraps
    llama-server overflow errors as "Internal embedding error" (the raw overflow body
    is only in container logs), so we cannot distinguish overflow from other 500s —
    splitting is the only safe recovery for multi-item batches.
    Single-item 500s: truncate and retry once, then zero-vector fallback.
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
            result = [item["embedding"] for item in response.json()["data"]]
            _cache_embed_dim(result)
            return result
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 503:
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


async def embed_query(text: str, scope: str | list[str] | None = None) -> list[float]:
    """Embed a search query with bounded jittered backoff on transient errors.

    Retry policy: exponential backoff with ±25% jitter, capped at _QUERY_RETRY_MAX_S.
    Retries on 502/503/429 and connection/timeout errors only.
    Raises EmbeddingTransientError when retries are exhausted so callers can
    distinguish transient unavailability from permanent errors.
    """
    # When scope is a list, only the first element is used for instruction formatting.
    if isinstance(scope, list):
        effective_scope = scope[0] if scope else None
    else:
        effective_scope = scope
    if _is_instruction_aware_model(_embed_model):
        instruction = _SCOPE_INSTRUCTIONS.get(
            effective_scope or "", _DEFAULT_INSTRUCTION
        )
        formatted = f"Instruct: {instruction}\nQuery: {text}"
    else:
        formatted = f"search_query: {text}"

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
                return data["data"][0]["embedding"]
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
    raise EmbeddingTransientError(
        f"Embedding query failed after {_QUERY_RETRY_ATTEMPTS} attempts "
        f"(model={_embed_model}, last_status={last_status})",
        model_id=_embed_model,
        attempts=_QUERY_RETRY_ATTEMPTS,
        last_status=last_status,
    )
