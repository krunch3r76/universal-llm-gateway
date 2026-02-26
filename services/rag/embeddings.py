from __future__ import annotations

import asyncio
import logging

import httpx

GATEWAY_URL = "http://localhost:9999"
EMBED_MODEL = "bge-m3-q8-0-8192-cpu"

_client = httpx.AsyncClient(timeout=60.0)
logger = logging.getLogger(__name__)

_PROBE_PAYLOAD = {"model": EMBED_MODEL, "input": ["probe"]}
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
                json=_PROBE_PAYLOAD,
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


async def embed_chunks(texts: list[str]) -> list[list[float]]:
    """Embed raw texts for indexing, batching to avoid overloading the endpoint."""
    all_embeddings: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH_SIZE):
        batch = texts[start : start + _EMBED_BATCH_SIZE]
        response = await _client.post(
            f"{GATEWAY_URL}/v1/embeddings",
            json={"model": EMBED_MODEL, "input": batch},
        )
        response.raise_for_status()
        data = response.json()
        all_embeddings.extend(item["embedding"] for item in data["data"])
    return all_embeddings


async def embed_query(text: str) -> list[float]:
    """Embed a search query. Prepends 'search_query: ' before sending to gateway."""
    response = await _client.post(
        f"{GATEWAY_URL}/v1/embeddings",
        json={"model": EMBED_MODEL, "input": [f"search_query: {text}"]},
    )
    response.raise_for_status()
    data = response.json()
    return data["data"][0]["embedding"]
