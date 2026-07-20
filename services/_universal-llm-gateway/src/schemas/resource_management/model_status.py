"""Pydantic schemas for per-model and catalog-wide model status API responses.

Provides status snapshots for individual models and aggregated maps used by
queue management endpoints that expose load state, usage, and error details.
"""

import time

from pydantic import BaseModel, ConfigDict, Field

from .literals import ModelStatus


class ModelStatusInfo(BaseModel):
    """Individual model status information"""

    status: ModelStatus = Field(..., description="Current model status")
    loaded: bool = Field(..., description="Whether model is loaded")
    enabled: bool = Field(..., description="Whether model is enabled in configuration")
    vram_usage_mb: int = Field(..., description="VRAM usage in MB")
    ram_usage_mb: int = Field(..., description="RAM usage in MB")
    current_inference_start: float | None = Field(
        None, description="Timestamp when current inference started"
    )
    last_inference_end: float | None = Field(
        None, description="Timestamp when last inference ended"
    )
    load_time: float | None = Field(None, description="Timestamp when model was loaded")
    error: str | None = Field(
        None, description="Error message if model is in error state"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "busy",
                "loaded": True,
                "enabled": True,
                "vram_usage_mb": 12000,
                "ram_usage_mb": 8000,
                "current_inference_start": 1703123456.789,
                "last_inference_end": None,
                "load_time": 1703123400.123,
                "error": None,
            }
        }
    )


class ModelStatusResponse(BaseModel):
    """Model status response schema"""

    models: dict[str, ModelStatusInfo] = Field(
        ..., description="Status information for all models"
    )
    timestamp: float = Field(
        default_factory=time.time, description="Response timestamp"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "models": {
                    "deepseek-coder-33b-awq": {
                        "status": "busy",
                        "loaded": True,
                        "enabled": True,
                        "vram_usage_mb": 12000,
                        "ram_usage_mb": 8000,
                        "current_inference_start": 1703123456.789,
                        "last_inference_end": None,
                        "load_time": 1703123400.123,
                        "error": None,
                    }
                },
                "timestamp": 1703123456.789,
            }
        }
    )


class SingleModelStatusResponse(BaseModel):
    """Single model status response schema"""

    model_id: str = Field(..., description="Model ID")
    status: ModelStatus = Field(..., description="Current model status")
    loaded: bool = Field(..., description="Whether model is loaded")
    enabled: bool = Field(..., description="Whether model is enabled in configuration")
    vram_usage_mb: int = Field(..., description="VRAM usage in MB")
    ram_usage_mb: int = Field(..., description="RAM usage in MB")
    current_inference_start: float | None = Field(
        None, description="Timestamp when current inference started"
    )
    last_inference_end: float | None = Field(
        None, description="Timestamp when last inference ended"
    )
    load_time: float | None = Field(None, description="Timestamp when model was loaded")
    error: str | None = Field(
        None, description="Error message if model is in error state"
    )
    timestamp: float = Field(
        default_factory=time.time, description="Response timestamp"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "model_id": "deepseek-coder-33b-awq",
                "status": "busy",
                "loaded": True,
                "enabled": True,
                "vram_usage_mb": 12000,
                "ram_usage_mb": 8000,
                "current_inference_start": 1703123456.789,
                "last_inference_end": 1703123440.456,
                "load_time": 1703123400.123,
                "error": None,
                "timestamp": 1703123456.789,
            }
        }
    )
