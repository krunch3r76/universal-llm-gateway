"""RAG article/source management passthrough endpoints.

Proxies article upsert and source deletion requests to the RAG service
through Stargate's administrative API, enabling MCP tools and other clients
to manage article metadata and source lifecycle without direct RAG access.
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


async def _proxy_rag_request(
    *,
    rag_url: str,
    method: str,
    endpoint: str,
    timeout: float,
    action_name: str,
    unavailable_detail: str,
    invalid_payload_detail: str,
    params: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Proxy a request to RAG and enforce a dict JSON response."""
    try:
        async with make_async_client(rag_url, timeout=timeout) as client:
            if method == "GET":
                response = await client.get(endpoint, params=params)
            elif method == "POST":
                response = await client.post(endpoint, json=json_body)
            elif method == "DELETE":
                response = await client.delete(endpoint, params=params)
            else:
                raise ValueError(f"Unsupported RAG proxy method: {method}")
        response.raise_for_status()
        payload = cast(object, response.json())
    except HTTPError as exc:
        status_code = getattr(exc.response, "status_code", "n/a")
        response_text = getattr(exc.response, "text", "")
        logger.warning(
            "RAG %s passthrough failed (rag_url=%s, status_code=%s, response=%s): %s",
            action_name,
            rag_url,
            status_code,
            response_text,
            exc,
        )
        raise HTTPException(status_code=503, detail=unavailable_detail) from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail=invalid_payload_detail)
    return cast(dict[str, Any], payload)


@router.post("/rag/article")
async def upsert_rag_article(
    body: dict[str, Any],
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> dict[str, Any]:
    """Proxy an article upsert request to the RAG service."""
    rag_url = resolve_rag_base_url()
    return await _proxy_rag_request(
        rag_url=rag_url,
        method="POST",
        endpoint="/article",
        timeout=10.0,
        action_name="article",
        unavailable_detail="RAG article endpoint unavailable via Stargate passthrough.",
        invalid_payload_detail="Invalid RAG article response payload.",
        json_body=body,
    )


@router.delete("/rag/source")
async def delete_rag_source(
    path: str,
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> dict[str, Any]:
    """Proxy a source deletion request to the RAG service."""
    rag_url = resolve_rag_base_url()
    return await _proxy_rag_request(
        rag_url=rag_url,
        method="DELETE",
        endpoint="/source",
        timeout=30.0,
        action_name="source delete",
        unavailable_detail=(
            "RAG source delete endpoint unavailable via Stargate passthrough."
        ),
        invalid_payload_detail="Invalid RAG source delete response payload.",
        params={"path": path},
    )


@router.get("/rag/orphaned_articles")
async def get_orphaned_articles(
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> dict[str, Any]:
    """Proxy orphaned-articles diagnostic query to the RAG service."""
    rag_url = resolve_rag_base_url()
    return await _proxy_rag_request(
        rag_url=rag_url,
        method="GET",
        endpoint="/orphaned_articles",
        timeout=15.0,
        action_name="orphaned articles",
        unavailable_detail=(
            "RAG orphaned_articles endpoint unavailable via passthrough."
        ),
        invalid_payload_detail="Invalid RAG orphaned_articles response payload.",
    )


@router.get("/rag/articles")
async def list_rag_articles(
    scope: str | None = None,
    include_abstract: bool = False,
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> dict[str, Any]:
    """Proxy structured article-listing queries to the RAG service."""
    rag_url = resolve_rag_base_url()
    params: dict[str, str] = {
        "include_abstract": "true" if include_abstract else "false",
        **({"scope": scope} if scope else {}),
    }
    return await _proxy_rag_request(
        rag_url=rag_url,
        method="GET",
        endpoint="/articles",
        timeout=20.0,
        action_name="article listing",
        unavailable_detail="RAG articles endpoint unavailable via passthrough.",
        invalid_payload_detail="Invalid RAG articles response payload.",
        params=params,
    )


@router.post("/rag/refresh_corpus_hints")
async def refresh_corpus_hints(
    body: dict[str, Any],
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> dict[str, Any]:
    """Proxy a corpus hints refresh request to the RAG service."""
    rag_url = resolve_rag_base_url()
    return await _proxy_rag_request(
        rag_url=rag_url,
        method="POST",
        endpoint="/refresh_corpus_hints",
        timeout=60.0,
        action_name="refresh corpus hints",
        unavailable_detail=(
            "RAG corpus hints refresh unavailable via Stargate passthrough."
        ),
        invalid_payload_detail="Invalid RAG corpus hints refresh response payload.",
        json_body=body,
    )


@router.delete("/rag/directory")
async def delete_rag_directory(
    path: str,
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> dict[str, Any]:
    """Proxy a directory deletion request to the RAG service."""
    rag_url = resolve_rag_base_url()
    return await _proxy_rag_request(
        rag_url=rag_url,
        method="DELETE",
        endpoint="/directory",
        timeout=60.0,
        action_name="directory delete",
        unavailable_detail="RAG directory delete endpoint unavailable via passthrough.",
        invalid_payload_detail="Invalid RAG directory delete response payload.",
        params={"path": path},
    )
