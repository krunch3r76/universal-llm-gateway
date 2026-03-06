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


_EMBED_BATCH_SIZE = 64
# Stay safely under llama.cpp's n_batch=8192; 4 chars ≈ 1 token for English text.
_MAX_BATCH_TOKENS = 7000
_CHARS_PER_TOKEN = 4

_EMBED_RETRY_ATTEMPTS = 3
_EMBED_RETRY_BACKOFF_S = 1.0


async def _post_embeddings(batch: list[str]) -> list[list[float]]:
    """POST a single batch to the embedding endpoint with retry on transient errors.

    Retries on connection failures and 503 (service temporarily unavailable).
    Does NOT retry on 500 — content errors (e.g. n_batch overflow) are permanent
    and should surface immediately rather than spin.
    """
    last_exc: Exception | None = None
    for attempt in range(_EMBED_RETRY_ATTEMPTS):
        try:
            response = await _client.post(
                f"{GATEWAY_URL}/v1/embeddings",
                json={"model": _embed_model, "input": batch},
            )
            response.raise_for_status()
            return [item["embedding"] for item in response.json()["data"]]
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
    raise last_exc  # type: ignore[misc]


async def embed_chunks(texts: list[str]) -> list[list[float]]:
    """Embed raw texts for indexing.

    Splits into sub-batches bounded by both count and estimated token total.
    ∀ batch: len ≤ _EMBED_BATCH_SIZE ∧ Σ(tokens) ≤ _MAX_BATCH_TOKENS.
    Prevents llama.cpp n_batch overflow when the aggregate input token count
    exceeds the physical batch size (8192 for bge-m3).
    """
    all_embeddings: list[list[float]] = []
    batch: list[str] = []
    batch_tokens = 0

    for text in texts:
        token_estimate = max(1, len(text) // _CHARS_PER_TOKEN)
        if batch and (
            len(batch) >= _EMBED_BATCH_SIZE
            or batch_tokens + token_estimate > _MAX_BATCH_TOKENS
        ):
            all_embeddings.extend(await _post_embeddings(batch))
            batch = []
            batch_tokens = 0
        batch.append(text)
        batch_tokens += token_estimate

    if batch:
        all_embeddings.extend(await _post_embeddings(batch))

    return all_embeddings


async def embed_query(text: str) -> list[float]:
    """Embed a search query. Prepends 'search_query: ' before sending to gateway."""
    response = await _client.post(
        f"{GATEWAY_URL}/v1/embeddings",
        json={"model": _embed_model, "input": [f"search_query: {text}"]},
    )
    response.raise_for_status()
    data = response.json()
    return data["data"][0]["embedding"]
