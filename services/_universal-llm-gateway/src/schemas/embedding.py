"""OpenAI Embeddings API compatible schemas."""

from pydantic import BaseModel, Field


class EmbeddingRequest(BaseModel):
    """Request schema for embeddings endpoint."""

    model: str = Field(..., description="Model ID for embeddings")
    input: str | list[str] = Field(..., description="Text(s) to embed")
    encoding_format: str | None = Field(
        default="float", description="Encoding format (float or base64)"
    )

    class Config:
        extra = "allow"  # Allow passthrough


class EmbeddingData(BaseModel):
    """Single embedding result."""

    object: str = Field(default="embedding")
    embedding: list[float] = Field(..., description="Embedding vector")
    index: int = Field(..., description="Index in input list")


class EmbeddingUsage(BaseModel):
    """Token usage for embedding request."""

    prompt_tokens: int = Field(..., description="Input tokens")
    total_tokens: int = Field(..., description="Total tokens (same as prompt)")


class EmbeddingResponse(BaseModel):
    """Response schema for embeddings endpoint."""

    object: str = Field(default="list")
    data: list[EmbeddingData] = Field(..., description="Embedding results")
    model: str = Field(..., description="Model used")
    usage: EmbeddingUsage = Field(..., description="Token usage")
