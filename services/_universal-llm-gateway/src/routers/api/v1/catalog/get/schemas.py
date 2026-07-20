"""Pydantic request and response models for catalog API read and write endpoints.

Defines wire shapes for full catalog responses, model summaries, profile patches,
and activated-context updates without embedding route handler logic.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CatalogResponse(BaseModel):
    """Response model for full catalog endpoint."""

    catalog_version: str
    catalog_type: str
    schema_version: int
    models: dict[str, Any]


class ModelEntryResponse(BaseModel):
    """Response model for individual model entry (V2)."""

    model_config = ConfigDict(populate_by_name=True)

    model_id: str
    schema_name: str = Field(alias="schema")  # V2: required
    metadata: dict[str, Any]
    download: dict[str, Any]
    loader: dict[str, Any] = {}  # V2: replaces base_loader
    devices: dict[str, Any] = {}  # V2: replaces configurations


class ProfileUpdate(BaseModel):
    """Request model for updating a profile's resource values (V2)."""

    context: int = Field(..., description="Context length (e.g., 4096, 8192)")
    device: str = Field(
        "gpu",
        description="Device type: gpu, cpu, or hybrid",
    )
    vram_mb: int | None = Field(None, description="VRAM usage in MB")
    ram_mb: int | None = Field(None, description="RAM usage in MB")
    n_gpu_layers: int | None = Field(None, description="Number of GPU layers")


class ActivatedContextsUpdate(BaseModel):
    """Request model for updating activated contexts."""

    activated_gpu_contexts: list[int] | None = Field(
        None, description="GPU context lengths to expose in /v1/models"
    )
    activated_cpu_contexts: list[int] | None = Field(
        None, description="CPU context lengths to expose in /v1/models"
    )


class UpdateResponse(BaseModel):
    """Response model for update operations."""

    status: str
    message: str
    model_id: str
    updated_fields: list[str] = []


class ModelSummary(BaseModel):
    """Simple model summary for listing."""

    model_id: str
    filename: str  # GGUF file or HF directory name
    hf_repo: str | None = None  # HuggingFace repo if available
    format: str
    display_name: str | None = None
    description: str | None = None


class ModelSummaryListResponse(BaseModel):
    """Response model for simple model listing."""

    models: list[ModelSummary]
    count: int
