"""
API request and response schemas for model catalog management.

Defines Pydantic models for management API endpoints.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

# === Request Schemas ===


class AddModelRequest(BaseModel):
    """Request to add a new model to the catalog."""

    model_key: str = Field(
        ...,
        description="Unique model identifier",
        examples=["llama-3-1-8b-instruct"],
    )
    config: dict[str, Any] = Field(
        ...,
        description="Catalog entry (metadata, download, configurations)",
    )
    allow_overwrite: bool = Field(
        default=True,
        description="If true, overwrite existing model (default: true)",
    )
    static: bool = Field(
        default=False,
        description="If true, write to static catalog (maintainer mode); otherwise dynamic",
    )


class UpdateModelRequest(BaseModel):
    """Request to update an existing model."""

    config: dict[str, Any] = Field(
        ..., description="Complete catalog entry for the model"
    )


class ReloadConfigRequest(BaseModel):
    """Request to reload catalog (optional body)."""

    force: bool = Field(
        default=False, description="Force reload even if no changes detected"
    )


# === Response Schemas ===


class ModelManagementResponse(BaseModel):
    """Standard response for model management operations."""

    status: Literal["success", "error"] = Field(..., description="Operation status")
    message: str = Field(..., description="Human-readable message")
    model_key: str | None = Field(None, description="Model identifier (if applicable)")
    version: str | None = Field(None, description="Catalog version")


class GetModelConfigResponse(BaseModel):
    """Response for getting a model's catalog entry."""

    model_key: str = Field(..., description="Model identifier")
    config: dict[str, Any] = Field(..., description="Complete catalog entry")
    version: str = Field(..., description="Catalog version")


class ReloadConfigResponse(BaseModel):
    """Response for catalog reload."""

    status: Literal["success"] = Field(..., description="Operation status")
    message: str = Field(..., description="Human-readable message")
    models_added: list[str] = Field(..., description="Model IDs that were added")
    models_removed: list[str] = Field(..., description="Model IDs that were removed")
    models_possibly_modified: list[str] = Field(
        ..., description="Model IDs that may have been modified"
    )
    version: str = Field(..., description="Catalog version")


# === List/Query Schemas ===


class ModelListItem(BaseModel):
    """Summary information for a model in list view."""

    model_key: str = Field(..., description="Model identifier")
    name: str = Field(..., description="Human-readable model name")
    format: str = Field(
        ..., description="Model format", examples=["gguf", "hf", "gptq", "awq"]
    )
    enabled: bool = Field(..., description="Whether model is enabled")
    openai_id: str = Field(..., description="OpenAI-compatible model ID")


class ListModelsResponse(BaseModel):
    """Response for listing all models."""

    models: list[ModelListItem] = Field(..., description="List of models in catalog")
    total_count: int = Field(..., description="Total number of models")
    version: str = Field(..., description="Catalog version")
