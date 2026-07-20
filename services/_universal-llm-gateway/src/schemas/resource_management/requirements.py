"""Pydantic schema for model hardware requirement and timing estimates.

Surfaces VRAM/RAM needs, quantization metadata, and estimated load/unload
durations so clients can plan resource allocation before requesting a load.
"""

import time

from pydantic import BaseModel, ConfigDict, Field


class ModelRequirementsResponse(BaseModel):
    """Model resource requirements response schema"""

    model_id: str = Field(..., description="Model ID")
    vram_required_mb: int | None = Field(None, description="Required VRAM in MB")
    ram_required_mb: int | None = Field(None, description="Required RAM in MB")
    estimated_load_time: float = Field(
        ..., description="Estimated loading time in seconds"
    )
    estimated_unload_time: float = Field(
        ..., description="Estimated unloading time in seconds"
    )
    quantization: str | None = Field(None, description="Model quantization type")
    model_size_mb: int | None = Field(None, description="Model file size in MB")
    training_context_length: int | None = Field(
        None, description="Training context length (historical reference only)"
    )
    timestamp: float = Field(
        default_factory=time.time, description="Response timestamp"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "model_id": "deepseek-coder-33b-awq",
                "vram_required_mb": 12000,
                "ram_required_mb": 8000,
                "estimated_load_time": 30.5,
                "estimated_unload_time": 5.2,
                "quantization": "awq",
                "model_size_mb": 15000,
                "context_length": 16384,
                "timestamp": 1703123456.789,
            }
        }
    )
