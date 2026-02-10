"""
Embeddings endpoint - /v1/embeddings

Routes embedding requests to appropriate Gateway via federation.
"""

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, Field
from universal_logging import get_logger

from ...dependencies import get_proxy
from ...stargate_core import StargateProxy

router = APIRouter(tags=["OpenAI Compatible"])
logger = get_logger(__name__)


class EmbeddingRequest(BaseModel):
    """Request schema for embeddings endpoint."""

    model: str = Field(..., description="Model ID for embeddings")
    input: str | list[str] = Field(..., description="Text(s) to embed")
    encoding_format: str | None = Field(default="float")

    class Config:
        extra = "allow"


@router.post("/embeddings")
async def create_embeddings(
    request: Request,
    embedding_request: EmbeddingRequest = Body(...),
    proxy: StargateProxy = Depends(get_proxy),
):
    """
    Generate embeddings via federated Gateway.

    Routes to appropriate Edge Gateway based on model availability.
    """
    # Normalize input
    input_texts = embedding_request.input
    if isinstance(input_texts, str):
        input_texts = [input_texts]

    model_id = embedding_request.model

    logger.info(f"Embedding request: model={model_id}, inputs={len(input_texts)}")

    # Process through proxy (handles routing and federation)
    return await proxy.process_embedding_request(
        model_id=model_id,
        input_texts=input_texts,
        request=request,
    )
