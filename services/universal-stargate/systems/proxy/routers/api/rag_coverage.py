"""RAG coverage passthrough endpoint.

Exposes the RAG /coverage aggregation through Stargate's administrative API.
Returns per-scope, per-prefix indexed file counts and last-indexed timestamps.
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


@router.get("/rag/coverage")
async def get_rag_coverage(
    current_user: dict[str, object] = Depends(get_auth_dependency),
) -> dict[str, Any]:
    """Return the RAG coverage payload with per-scope prefix counts.

    Expected shape:
    ``{"scopes": {"<scope>": {"prefixes": [...], "total_indexed": <int>}}}``
    """
    del current_user
    rag_url = resolve_rag_base_url()
    try:
        async with make_async_client(rag_url, timeout=10.0) as client:
            response = await client.get("/coverage")
        response.raise_for_status()
        payload_obj = cast(object, response.json())
    except HTTPError as exc:
        logger.warning("RAG coverage passthrough failed (rag_url=%s): %s", rag_url, exc)
        raise HTTPException(
            status_code=503,
            detail="RAG coverage endpoint unavailable via Stargate passthrough.",
        ) from exc

    if not isinstance(payload_obj, dict):
        logger.warning(
            "RAG coverage passthrough received invalid payload type: %s",
            type(payload_obj).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="Invalid RAG coverage payload type.",
        )

    return cast(dict[str, Any], payload_obj)
