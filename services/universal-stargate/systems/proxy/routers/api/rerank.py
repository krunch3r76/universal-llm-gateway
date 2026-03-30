"""
Rerank endpoint — /api/v1/rerank

Routes rerank requests to appropriate Gateway via federation.
Nonstandard endpoint — lives under /api/v1, not /v1.
"""

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, Field
from universal_logging import get_logger

from ...dependencies import get_proxy
from ...stargate_core import StargateProxy

router = APIRouter(tags=["Reranking"])
logger = get_logger(__name__)


class RerankRequest(BaseModel):
    """
    Payload for Stargate's nonstandard rerank route under `/api/v1/rerank`.

    Includes the reranker model identifier, one query string, and the passages
    that should be scored against that query.
    """

    model: str = Field(..., description="Reranker model ID")
    query: str = Field(..., description="Query to score against passages")
    passages: list[str] = Field(..., description="Passages to score")

    class Config:
        extra = "allow"


@router.post("/rerank")
async def create_rerank(
    request: Request,
    rerank_request: RerankRequest = Body(...),
    proxy: StargateProxy = Depends(get_proxy),
):
    """
    Score (query, passage) pairs via federated Gateway.

    This is a Stargate-specific API route under `/api/v1/rerank`, not an
    OpenAI-compatible `/v1/*` endpoint.
    Routes to appropriate Edge Gateway based on model availability.
    """
    model_id = rerank_request.model
    query = rerank_request.query
    passages = rerank_request.passages

    logger.info("Rerank request: model=%s, passages=%d", model_id, len(passages))

    return await proxy.process_rerank_request(
        model_id=model_id,
        query=query,
        passages=passages,
        request=request,
    )
