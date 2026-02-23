import httpx

GATEWAY_URL = "http://localhost:9999"
EMBED_MODEL = "nomic-embed-text-v1-5-q8-0-2048-cpu"

_client = httpx.AsyncClient(timeout=60.0)


async def embed_chunks(texts: list[str]) -> list[list[float]]:
    """Embed raw texts for indexing. Gateway catalog adds search_document: prefix automatically."""
    response = await _client.post(
        f"{GATEWAY_URL}/v1/embeddings",
        json={"model": EMBED_MODEL, "input": texts},
    )
    response.raise_for_status()
    data = response.json()
    return [item["embedding"] for item in data["data"]]


async def embed_query(text: str) -> list[float]:
    """Embed a search query. Prepends 'search_query: ' before sending to gateway."""
    response = await _client.post(
        f"{GATEWAY_URL}/v1/embeddings",
        json={"model": EMBED_MODEL, "input": [f"search_query: {text}"]},
    )
    response.raise_for_status()
    data = response.json()
    return data["data"][0]["embedding"]
