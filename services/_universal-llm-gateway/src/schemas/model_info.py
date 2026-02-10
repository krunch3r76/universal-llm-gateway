"""Model information and metadata schemas"""

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InferenceEngineInfo(BaseModel):
    """Information about the inference engine for a model"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "engine_name": "transformers",
                "format": "awq",
                "input_format": "messages",
                "expected_field": "messages",
                "uses_chat_template": False,
                "specification": {
                    "input_format": "messages",
                    "uses_chat_template": False,
                    "expected_field": "messages",
                },
            }
        }
    )

    engine_name: str = Field(..., description="Name of the inference engine")
    format: str = Field(..., description="Model format (awq, gptq, gguf, etc.)")
    input_format: str = Field(
        ..., description="Expected input format (messages, prompt)"
    )
    expected_field: str = Field(..., description="Expected field name in request")
    uses_chat_template: bool = Field(
        ..., description="Whether the model uses chat templates"
    )
    specification: dict[str, Any] = Field(..., description="Full specification details")

    # NO degradation fields - strict validation only


class ChatTemplateInfo(BaseModel):
    """Chat template information schema"""

    exists: bool = Field(
        ..., description="Whether the model has an existing chat template"
    )
    content: str | None = Field(None, description="Chat template content")
    supports_system_role: bool = Field(
        False, description="Whether the template supports system role"
    )
    source: str | None = Field(
        None, description="Source of the template (e.g., tokenizer_config.json)"
    )


class ModelInfo(BaseModel):
    """Model information schema with support for both static and runtime data"""

    model_config = ConfigDict(
        protected_namespaces=(),
        json_schema_extra={
            "example": {
                "id": "wizard-vicuna-30b-awq",
                "object": "model",
                "created": 1699000000,
                "owned_by": "universal-llm-gateway",
                "name": "Wizard Vicuna 30B AWQ",
                "format": "awq",
                "enabled": True,
                "training_context_length": 4096,
                "estimated_vram_mb": 18000,
                "specialties": ["instruction_following", "conversation"],
                "quantization": "AWQ",
                "parameters": "30B",
                "has_chat_template": False,
                "model_type": "wizard-vicuna",
                "chat_template": {
                    "exists": False,
                    "content": None,
                    "supports_system_role": False,
                },
                "inference_engine": {
                    "engine_name": "transformers",
                    "format": "awq",
                    "input_format": "messages",
                    "expected_field": "messages",
                    "uses_chat_template": False,
                },
                "supported_parameters": ["temperature", "top_p", "max_tokens"],
                "loader_type": "awq",
                "path": "/models/wizard-vicuna-30b-awq",
                "token_counting_enabled": True,
            }
        },
    )

    # Core fields (always available)
    id: str = Field(..., description="Model identifier")
    object: str = Field(default="model", description="Object type")
    created: int | None = Field(
        default_factory=lambda: int(time.time()), description="Creation timestamp"
    )
    owned_by: str = Field(
        default="universal-llm-gateway", description="Owner of the model"
    )
    permission: list[dict[str, Any]] = Field(
        default_factory=list, description="Model permissions"
    )

    # Static metadata (from config)
    name: str | None = Field(None, description="Human-readable model name")
    format: str = Field(..., description="Model format (e.g., gguf, gptq, awq)")
    enabled: bool = Field(True, description="Whether the model is enabled")
    training_context_length: int | None = Field(
        None, description="Training context length (historical reference only)"
    )
    estimated_vram_mb: int | None = Field(
        None, description="Estimated VRAM usage in MB"
    )
    specialties: list[str] | None = Field(None, description="Model specialties")
    quantization: str | None = Field(None, description="Quantization method")
    parameters: str | None = Field(None, description="Parameter count")
    has_chat_template: bool = Field(
        default=False, description="Whether model has built-in chat template"
    )

    # Runtime metadata (for debug/admin endpoints - can be None)
    model_type: str | None = Field(
        None, description="Model type (e.g., wizard-vicuna, llama)"
    )
    chat_template: ChatTemplateInfo | None = Field(
        None, description="Chat template information"
    )
    inference_engine: InferenceEngineInfo | None = Field(
        None, description="Inference engine information"
    )
    supported_parameters: list[str] | None = Field(
        None, description="List of supported parameters"
    )
    loader_type: str | None = Field(None, description="Loader type used for this model")
    path: str | None = Field(None, description="Path to model files")
    token_counting_enabled: bool = Field(
        False, description="Whether token counting is available"
    )

    # Stargate-required fields for routing and processing
    input_schema: str = Field(
        default="prompt", description="Input schema: 'prompt' or 'messages'"
    )
    parameter_defaults: dict[str, Any] = Field(
        default_factory=dict, description="Default parameter values for this model"
    )
    middleware_config: dict[str, Any] = Field(
        default_factory=dict, description="Middleware configuration for this model"
    )
    ram_usage: int = Field(default=0, description="RAM usage in MB (0 = unknown)")
    vram_usage: int = Field(default=0, description="VRAM usage in MB (0 = unknown)")
    context_length: int | None = Field(
        None, description="Active context length for this model profile"
    )


class ModelConfigurationsResponse(BaseModel):
    """Response schema for bulk model configurations endpoint"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "models": {
                    "wizard-vicuna-7b": {
                        "id": "wizard-vicuna-7b",
                        "model_type": "wizard-vicuna",
                        "chat_template": {
                            "exists": False,
                            "content": None,
                            "supports_system_role": False,
                        },
                    }
                }
            }
        }
    )

    models: dict[str, ModelInfo] = Field(
        ..., description="Dictionary of model configurations"
    )


class ModelValidationResult(BaseModel):
    """Model file validation result"""

    model_config = ConfigDict(protected_namespaces=())

    model_id: str = Field(..., description="Model identifier")
    exists: bool = Field(..., description="Whether model file exists")
    path: str = Field(..., description="Model file path")
    size_mb: float | None = Field(None, description="File size in MB")
    readable: bool = Field(..., description="Whether file is readable")
    error: str | None = Field(None, description="Error message if validation failed")


class ModelValidationReport(BaseModel):
    """Complete model validation report"""

    total_models: int = Field(..., description="Total number of models checked")
    valid_models: int = Field(..., description="Number of valid models")
    results: list[ModelValidationResult] = Field(
        ..., description="Individual validation results"
    )
