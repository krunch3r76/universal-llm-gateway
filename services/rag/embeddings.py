from __future__ import annotations

import asyncio
import logging

import httpx

GATEWAY_URL = "http://localhost:9999"

_client = httpx.AsyncClient(timeout=60.0)
logger = logging.getLogger(__name__)

_embed_model: str = "bge-m3-q8-0-8192-cpu"
_probe_payload: dict[str, object] = {"model": _embed_model, "input": ["probe"]}


def configure(model_id: str) -> None:
    """Set the embedding model ID from config. Call once at startup before any embed calls."""
    global _embed_model, _probe_payload
    if not model_id or not model_id.strip():
        raise ValueError(f"configure() received blank model_id: {model_id!r}")
    _embed_model = model_id
    _probe_payload = {"model": _embed_model, "input": ["probe"]}
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
    """Detect whether the configured embedding model supports Instruct:/Query: format."""
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
        return int(int(parts[1]) * _N_CTX_HEADROOM)
    return _FALLBACK_MAX_BATCH_TOKENS


_EMBED_RETRY_ATTEMPTS = 3
_EMBED_RETRY_BACKOFF_S = 1.0

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
        except Exception:
            pass
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
        delay = _EMBED_RETRY_BACKOFF_S * (2**attempt)
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


async def embed_query(text: str, scope: str | list[str] | None = None) -> list[float]:
    """Embed a search query.

    For instruction-aware models (Qwen3-Embedding): uses Instruct:/Query: format
    with scope-specific instructions. For legacy models (bge-m3): uses search_query: prefix.
    When scope is a list, the first scope is used for instruction selection.
    """
    if isinstance(scope, list):
        effective_scope = scope[0] if scope else None
    else:
        effective_scope = scope
    if _is_instruction_aware_model(_embed_model):
        instruction = _SCOPE_INSTRUCTIONS.get(effective_scope or "", _DEFAULT_INSTRUCTION)
        formatted = f"Instruct: {instruction}\nQuery: {text}"
    else:
        formatted = f"search_query: {text}"

    response = await _client.post(
        f"{GATEWAY_URL}/v1/embeddings",
        json={"model": _embed_model, "input": [formatted]},
    )
    response.raise_for_status()
    data = response.json()
    return data["data"][0]["embedding"]
