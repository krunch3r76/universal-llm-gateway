"""RAG article metadata passthrough endpoint.

Proxies article upsert requests to the RAG service through Stargate's
administrative API, enabling MCP tools and other clients to manage article
metadata without direct RAG access.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException
from httpx import HTTPError
from transport_utils.rag_client import make_async_client, resolve_rag_base_url
from universal_logging import get_logger

from ...dependencies import get_auth_dependency

logger = get_logger(__name__)
router = APIRouter(tags=["rag"])


@router.post("/rag/article")
async def upsert_rag_article(
    body: dict[str, Any],
    current_user: dict[str, object] = Depends(get_auth_dependency),
) -> dict[str, object]:
    """Proxy an article upsert request to the RAG service."""
    del current_user
    rag_url = resolve_rag_base_url()
    try:
        async with make_async_client(rag_url, timeout=10.0) as client:
            response = await client.post("/article", json=body)
        response.raise_for_status()
        payload = cast(object, response.json())
    except HTTPError as exc:
        logger.warning("RAG article passthrough failed (rag_url=%s): %s", rag_url, exc)
        raise HTTPException(
            status_code=503,
            detail="RAG article endpoint unavailable via Stargate passthrough.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502, detail="Invalid RAG article response payload."
        )
    return cast(dict[str, object], payload)
