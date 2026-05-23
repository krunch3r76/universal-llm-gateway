"""RAG chunks_by_index passthrough endpoint.

Exposes RAG /chunks_by_index through Stargate so resolvers and migration
scripts can fetch chunk text by (source, chunk_index) pairs.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Body, Depends, HTTPException
from httpx import HTTPError
from transport_utils import make_async_client, resolve_rag_base_url
from universal_logging import get_logger

from ...dependencies import get_auth_dependency

logger = get_logger(__name__)
router = APIRouter(tags=["rag"])


@router.post("/rag/chunks_by_index")
async def post_rag_chunks_by_index(
    body: dict[str, Any] = Body(...),
    current_user: dict[str, object] = Depends(get_auth_dependency),
) -> dict[str, Any]:
    """Forward {groups:[{source, chunk_indices}]} POSTs to RAG /chunks_by_index."""
    del current_user
    rag_url = resolve_rag_base_url()
    try:
        async with make_async_client(rag_url, timeout=15.0) as client:
            response = await client.post("/chunks_by_index", json=body)
        response.raise_for_status()
        payload_obj = cast(object, response.json())
    except HTTPError as exc:
        logger.warning(
            "RAG chunks_by_index passthrough failed (rag_url=%s): %s", rag_url, exc
        )
        raise HTTPException(
            status_code=503,
            detail="RAG chunks_by_index endpoint unavailable via Stargate passthrough.",
        ) from exc

    if not isinstance(payload_obj, dict):
        raise HTTPException(
            status_code=502,
            detail="Invalid RAG chunks_by_index payload type.",
        )

    return cast(dict[str, Any], payload_obj)
