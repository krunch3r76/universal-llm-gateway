"""OpenAI-compatible model schemas with minimum required fields"""

from typing import Any

from pydantic import BaseModel, Field


class OpenAIModelInfo(BaseModel):
    """Minimal model information per OpenAI API specification"""

    id: str = Field(..., description="Model identifier")
    object: str = Field(default="model", description="Object type")
    owned_by: str = Field(..., description="Owner of the model")
    permission: list[str] = Field(default=["generate"], description="Model permissions")


class OpenAIModelListResponse(BaseModel):
    """Response schema for OpenAI-compatible models listing endpoint"""

    object: str = Field(default="list", description="Object type")
    data: list[OpenAIModelInfo] = Field(..., description="List of available models")


class ExtendedModelInfo(BaseModel):
    """Extended model information including OpenAI API fields and standardized metadata"""

    # OpenAI API fields
    id: str = Field(..., description="Model identifier")
    object: str = Field(default="model", description="Object type")
    owned_by: str = Field(..., description="Owner of the model")
    permission: list[str] = Field(default=["generate"], description="Model permissions")

    # Standardized model metadata
    training_context_length: int | None = Field(
        None, description="Training context length (historical reference only)"
    )
    input_schema: str | None = Field(
        None, description="Input schema: 'prompt' or 'messages'"
    )
    training_cutoff_year: int | None = Field(
        None, description="Training data cutoff year"
    )
    model_family: str | None = Field(None, description="Model family")
    quantization: str | None = Field(None, description="Quantization method")
    architecture: str | None = Field(None, description="Base architecture")
    parameters: int | None = Field(None, description="Number of parameters")
    release_date: str | None = Field(None, description="Release date")
    description: str | None = Field(None, description="Model description")
    capabilities: dict[str, Any] | None = Field(
        None, description="Structured capabilities"
    )

    # Loader-specific fields
    name: str | None = Field(None, description="Human-readable model name")
    format: str | None = Field(None, description="Model format (gguf, awq, etc.)")
    enabled: bool | None = Field(None, description="Whether model is enabled")
    path: str | None = Field(None, description="Path to model files")
    ram_usage: int | None = Field(None, description="RAM usage in MB")
    vram_usage: int | None = Field(None, description="VRAM usage in MB")


class ExtendedModelListResponse(BaseModel):
    """Response schema for extended models listing endpoint"""

    object: str = Field(default="list", description="Object type")
    data: list[ExtendedModelInfo] = Field(
        ..., description="List of available models with extended information"
    )


class ComprehensiveModelInfo(BaseModel):
    """Complete model information including ALL fields from model_loaders.yaml"""

    # OpenAI API fields
    id: str = Field(..., description="Model identifier")
    object: str = Field(default="model", description="Object type")
    owned_by: str = Field(..., description="Owner of the model")
    permission: list[str] = Field(default=["generate"], description="Model permissions")

    # Basic model fields
    name: str | None = Field(None, description="Human-readable model name")
    format: str | None = Field(None, description="Model format (gguf, awq, etc.)")
    enabled: bool | None = Field(None, description="Whether model is enabled")
    path: str | None = Field(None, description="Path to model files")

    # Resource usage and loader type (critical for Stargate routing)
    ram_usage: int | None = Field(None, description="RAM usage in MB")
    vram_usage: int | None = Field(None, description="VRAM usage in MB")
    loader_type: str | None = Field(
        None,
        description="Loader type for routing decisions (llama_cpp_cpu, llama_cpp_gpu, llama_cpp_hybrid)",
    )

    # Standardized metadata
    training_context_length: int | None = Field(
        None, description="Training context length (historical reference only)"
    )
    context_length: int | None = Field(
        None, description="Context length for this model profile"
    )
    input_schema: str | None = Field(
        None, description="Input schema: 'prompt' or 'messages'"
    )
    training_cutoff_year: int | None = Field(
        None, description="Training data cutoff year"
    )
    model_family: str | None = Field(None, description="Model family")
    quantization: str | None = Field(None, description="Quantization method")
    architecture: str | None = Field(None, description="Base architecture")
    parameters: int | None = Field(None, description="Number of parameters")
    release_date: str | None = Field(None, description="Release date")
    description: str | None = Field(None, description="Model description")
    capabilities: dict[str, Any] | None = Field(
        None, description="Structured capabilities"
    )

    # Loader-specific configuration - the actual config used by workers and inference engines
    loader_config: dict[str, Any] | None = Field(
        None,
        description="Compiled loader configuration (base_loader + profile-specific loader) - same as used internally by workers",
    )

    # Additional fields removed - supports_chat is redundant with input_schema and supports_chat_history


class ComprehensiveModelListResponse(BaseModel):
    """Response schema for comprehensive models listing endpoint"""

    object: str = Field(default="list", description="Object type")
    data: list[ComprehensiveModelInfo] = Field(
        ..., description="List of available models with complete information"
    )
