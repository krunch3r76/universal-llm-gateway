"""Model information and metadata schemas"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ModelInfo(BaseModel):
    """Model information schema - flexible for both basic and detailed responses"""

    model_config = ConfigDict(
        protected_namespaces=(),
        extra="allow",  # Allow additional fields from gateway
        json_schema_extra={
            "example": {
                "id": "deepseek-chat-67b-q4km",
                "object": "model",
                "owned_by": "universal-llm-gateway",
                "permission": ["generate"],
            }
        },
    )

    # Core required fields (minimal for basic gateway response)
    id: str = Field(..., description="Model identifier")
    object: str = Field(default="model", description="Object type")
    owned_by: str = Field(..., description="Owner of the model")
    permission: list[str] = Field(default_factory=list, description="Model permissions")

    # Optional fields that may be present in detailed responses
    created: int | None = Field(None, description="Creation timestamp")
    name: str | None = Field(None, description="Human-readable model name")
    format: str | None = Field(None, description="Model format (e.g., gguf, gptq, awq)")
    enabled: bool | None = Field(None, description="Whether the model is enabled")
    context_length: int | None = Field(None, description="Maximum context length")
    input_schema: str | None = Field(
        None, description="Input schema - 'messages' or 'prompt'"
    )

    # New schema fields (all optional)
    root: str | None = Field(None, description="Root model identifier")
    parent: str | None = Field(None, description="Parent model identifier")
    status: str | None = Field(None, description="Model status")
    version: str | None = Field(None, description="Model version")
    capabilities: dict[str, Any] | None = Field(None, description="Model capabilities")
    tags: list[str] | None = Field(None, description="Model tags")
    description: str | None = Field(None, description="Model description")
    license: str | None = Field(None, description="Model license")
    size_mb: float | None = Field(None, description="Model size in MB")
    download_url: str | None = Field(None, description="Model download URL")
    documentation_url: str | None = Field(None, description="Model documentation URL")
    examples: list[dict[str, Any]] | None = Field(None, description="Usage examples")
    benchmarks: dict[str, Any] | None = Field(
        None, description="Performance benchmarks"
    )
    requirements: dict[str, Any] | None = Field(None, description="System requirements")
    compatibility: dict[str, Any] | None = Field(
        None, description="Compatibility information"
    )
    usage: dict[str, Any] | None = Field(None, description="Usage information")
    pricing: dict[str, Any] | None = Field(None, description="Pricing information")
    specialties: list[str] | None = Field(None, description="Model specialties")


class ModelListResponse(BaseModel):
    """Response schema for models listing endpoint - flexible for basic and detailed responses"""

    model_config = ConfigDict(
        extra="allow",  # Allow additional fields from gateway
        json_schema_extra={
            "example": {
                "object": "list",
                "data": [
                    {
                        "id": "deepseek-chat-67b-q4km",
                        "object": "model",
                        "owned_by": "universal-llm-gateway",
                        "permission": ["generate"],
                    }
                ],
            }
        },
    )

    # Core response fields (required)
    object: str = Field(default="list", description="Object type")
    data: list[ModelInfo] = Field(..., description="List of available models")

    # Optional pagination and metadata fields (for detailed responses)
    total_count: int | None = Field(None, description="Total number of models")
    has_more: bool | None = Field(
        None, description="Whether there are more models available"
    )
    next_cursor: str | None = Field(None, description="Cursor for pagination")
    prev_cursor: str | None = Field(None, description="Previous cursor for pagination")
    filters: dict[str, Any] | None = Field(None, description="Applied filters")
    sort: dict[str, Any] | None = Field(None, description="Sorting information")
    metadata: dict[str, Any] | None = Field(None, description="Response metadata")
