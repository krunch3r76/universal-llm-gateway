"""Pydantic schemas for aggregate resource status and per-model resource details.

Covers VRAM/RAM availability, loaded and busy model lists, and nested model
detail payloads returned by the gateway resource status API endpoint.
"""

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .literals import ModelStatus


class ModelResourceUsage(BaseModel):
    """Resource usage information for a specific model"""

    vram_usage_mb: int = Field(..., description="VRAM usage in MB")
    ram_usage_mb: int = Field(..., description="RAM usage in MB")
    current_inference_start: float | None = Field(
        None, description="Timestamp when current inference started"
    )
    last_inference_end: float | None = Field(
        None, description="Timestamp when last inference ended"
    )
    load_time: float | None = Field(None, description="Timestamp when model was loaded")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "vram_usage_mb": 12000,
                "ram_usage_mb": 8000,
                "current_inference_start": 1703123456.789,
                "last_inference_end": 1703123440.456,
                "load_time": 1703123400.123,
            }
        }
    )


class ModelDetails(BaseModel):
    """Detailed model information including status"""

    status: ModelStatus = Field(..., description="Current model status")
    current_inference_start: float | None = Field(
        None, description="Timestamp when current inference started"
    )
    last_inference_end: float | None = Field(
        None, description="Timestamp when last inference ended"
    )
    load_time: float | None = Field(None, description="Timestamp when model was loaded")
    last_inference_time: float | None = Field(
        None, description="Timestamp of the last inference (used for LRU eviction)"
    )
    ram_usage: int | None = Field(
        None, description="RAM usage in MB (from YAML profile resources)"
    )
    vram_usage: int | None = Field(
        None, description="VRAM usage in MB (from YAML profile resources)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "busy",
                "current_inference_start": 1703123456.789,
                "last_inference_end": None,
                "load_time": 1703123400.123,
                "last_inference_time": 1703123456.789,
                "ram_usage": 392,
                "vram_usage": 23000,
            }
        }
    )


class ResourceStatusResponse(BaseModel):
    """Resource status response schema"""

    total_vram_mb: int = Field(..., description="Total VRAM available in MB")
    available_vram_mb: int = Field(..., description="Available VRAM in MB")
    total_ram_mb: int = Field(..., description="Total RAM available in MB")
    available_ram_mb: int = Field(..., description="Available RAM in MB")
    loaded_models: list[str] = Field(
        ..., description="List of currently loaded model IDs"
    )
    busy_models: list[str] = Field(
        ..., description="List of models currently processing inference"
    )
    model_details: dict[str, ModelDetails] = Field(
        ..., description="Detailed information for each loaded model"
    )
    timestamp: float = Field(
        default_factory=time.time, description="Response timestamp"
    )

    # Debug information for VRAM troubleshooting
    debug_info: dict[str, Any] | None = Field(
        None, description="Debug information for resource troubleshooting"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total_vram_mb": 24000,
                "available_vram_mb": 12000,
                "total_ram_mb": 64000,
                "available_ram_mb": 32000,
                "loaded_models": ["deepseek-coder-33b-awq", "llama-2-7b-chat"],
                "busy_models": ["deepseek-coder-33b-awq"],
                "model_details": {
                    "deepseek-coder-33b-awq": {
                        "status": "busy",
                        "current_inference_start": 1703123456.789,
                        "last_inference_end": None,
                        "load_time": 1703123400.123,
                    }
                },
                "timestamp": 1703123456.789,
            }
        }
    )
