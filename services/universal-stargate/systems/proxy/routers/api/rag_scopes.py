"""RAG scope catalog passthrough endpoint.

Exposes the RAG /scopes registry through Stargate's administrative API.
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from httpx import HTTPError
from transport_utils.rag_client import make_async_client, resolve_rag_base_url
from universal_logging import get_logger

from ...dependencies import get_auth_dependency

logger = get_logger(__name__)
router = APIRouter(tags=["rag"])


@router.get("/rag/scopes")
async def list_rag_scopes(
    current_user: dict[str, object] = Depends(get_auth_dependency),
) -> dict[str, object]:
    """Return current RAG scope registry from the configured RAG transport."""
    del current_user
    rag_url = resolve_rag_base_url()
    try:
        async with make_async_client(rag_url, timeout=5.0) as client:
            response = await client.get("/scopes")
        response.raise_for_status()
        payload_obj = cast(object, response.json())
    except HTTPError as exc:
        logger.warning("RAG scopes passthrough failed (rag_url=%s): %s", rag_url, exc)
        raise HTTPException(
            status_code=503,
            detail="RAG scope catalog unavailable via Stargate passthrough.",
        ) from exc

    if not isinstance(payload_obj, dict):
        raise HTTPException(
            status_code=502,
            detail="Invalid RAG scope catalog payload type.",
        )

    scopes = payload_obj.get("scopes")
    if not isinstance(scopes, dict):
        raise HTTPException(
            status_code=502,
            detail="Invalid RAG scope catalog payload schema.",
        )

    return {"scopes": scopes}
