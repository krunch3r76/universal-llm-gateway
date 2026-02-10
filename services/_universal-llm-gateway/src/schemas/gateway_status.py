"""Schemas for rich gateway status API for proxy orchestration

This module provides comprehensive schemas for the detailed gateway status API
that enables proxy orchestration decisions. These schemas provide all the data
needed for intelligent request routing, load balancing, and resource management.
"""

from typing import Literal

from pydantic import BaseModel, Field

# Type definitions
ModelStatusType = Literal[
    "not_loaded", "loading", "loaded", "busy", "unloading", "error"
]
InferenceStateType = Literal["token_counting", "generating"]
HealthStatusType = Literal["healthy", "degraded", "unhealthy"]


class ModelResourceUsage(BaseModel):
    """Resource usage for a specific model"""

    vram_mb: int = Field(..., description="VRAM usage in MB")
    ram_mb: int = Field(..., description="RAM usage in MB")


class ModelStatusDetail(BaseModel):
    """Detailed status for a specific model"""

    status: ModelStatusType = Field(..., description="Current model status")
    inference_state: InferenceStateType | None = Field(
        None,
        description=(
            "Inference state when status is 'busy' (token_counting or generating)"
        ),
    )
    resource_usage: ModelResourceUsage = Field(
        ..., description="Current resource usage"
    )
    error_message: str | None = Field(
        None, description="Error message if status is 'error'"
    )


class OperationsInProgress(BaseModel):
    """Models currently being loaded or unloaded"""

    loading: list[str] = Field(
        default_factory=list, description="Model IDs currently loading"
    )
    unloading: list[str] = Field(
        default_factory=list, description="Model IDs currently unloading"
    )


class GatewayHealthInfo(BaseModel):
    """Gateway health and capacity information"""

    status: HealthStatusType = Field(..., description="Overall gateway health status")
    concurrent_requests: int = Field(
        ..., description="Number of requests currently being processed"
    )
    max_concurrent_requests: int = Field(
        ..., description="Maximum concurrent requests gateway can handle"
    )
    max_concurrent_workers: int = Field(
        default=1,
        description="Maximum concurrent workers (models) this gateway can handle",
    )
    supports_multi_model: bool = Field(
        default=False,
        description=(
            "Whether this gateway supports loading multiple models simultaneously"
        ),
    )


class ResourceInfo(BaseModel):
    """System resource information"""

    total_vram_mb: int = Field(..., description="Total VRAM available")
    available_vram_mb: int = Field(..., description="Currently available VRAM")
    total_ram_mb: int = Field(..., description="Total RAM available")
    available_ram_mb: int = Field(..., description="Currently available RAM")


class QueueInfo(BaseModel):
    """Request queue information"""

    pending_requests: int = Field(
        default=0, description="Number of requests waiting in queue"
    )


class DetailedStatusResponse(BaseModel):
    """Comprehensive gateway status for proxy orchestration"""

    gateway_id: str = Field(..., description="Unique gateway identifier")
    timestamp: float = Field(..., description="Status timestamp")
    gateway_health: GatewayHealthInfo = Field(
        ..., description="Gateway health and capacity"
    )
    resources: ResourceInfo = Field(..., description="System resource availability")
    models: dict[str, ModelStatusDetail] = Field(
        default_factory=dict, description="Status of all tracked models"
    )
    operations_in_progress: OperationsInProgress = Field(
        default_factory=OperationsInProgress,
        description="Models currently being loaded/unloaded",
    )
    queue_info: QueueInfo = Field(
        default_factory=QueueInfo, description="Current queue status"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "gateway_id": "gateway-1",
                "timestamp": 1703123456.789,
                "gateway_health": {
                    "status": "healthy",
                    "concurrent_requests": 2,
                    "max_concurrent_requests": 8,
                    "max_concurrent_workers": 1,
                    "supports_multi_model": False,
                },
                "resources": {
                    "total_vram_mb": 24000,
                    "available_vram_mb": 8000,
                    "total_ram_mb": 64000,
                    "available_ram_mb": 32000,
                },
                "models": {
                    "llama-70b": {
                        "status": "busy",
                        "inference_state": "token_counting",
                        "resource_usage": {"vram_mb": 16000, "ram_mb": 8000},
                    },
                    "deepseek-33b": {
                        "status": "loaded",
                        "inference_state": None,
                        "resource_usage": {"vram_mb": 12000, "ram_mb": 6000},
                    },
                },
                "operations_in_progress": {
                    "loading": ["new-model-7b"],
                    "unloading": ["old-model-13b"],
                },
                "queue_info": {"pending_requests": 1},
            }
        }
    }


# Model Requirements Schemas
class ResourceRequirements(BaseModel):
    """Resource requirements for a model"""

    vram_mb: int | None = Field(None, description="Required VRAM in MB")
    ram_mb: int | None = Field(None, description="Required RAM in MB")


class ModelCapabilities(BaseModel):
    """Model capabilities and features"""

    context_length: int | None = Field(None, description="Maximum context length")
    quantization: str | None = Field(
        None, description="Quantization type (awq, gptq, gguf)"
    )
    supports_streaming: bool = Field(
        True, description="Whether model supports streaming responses"
    )


class ModelRequirementsResponse(BaseModel):
    """Model resource requirements and capabilities"""

    model_id: str = Field(..., description="Model identifier")
    resource_requirements: ResourceRequirements = Field(
        ..., description="Resource requirements"
    )
    capabilities: ModelCapabilities = Field(..., description="Model capabilities")

    model_config = {
        "json_schema_extra": {
            "example": {
                "model_id": "llama-70b",
                "resource_requirements": {"vram_mb": 16000, "ram_mb": 8000},
                "capabilities": {
                    "context_length": 32768,
                    "quantization": "awq",
                    "supports_streaming": True,
                },
            }
        }
    }
