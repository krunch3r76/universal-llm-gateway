"""Pydantic schemas for model load and unload request and response payloads.

Defines priority and force flags for load/unload operations plus success,
error, and timing fields returned when the gateway initiates lifecycle changes.
"""

import time

from pydantic import BaseModel, ConfigDict, Field

from .literals import PriorityLevel


class ModelLoadRequest(BaseModel):
    """Model loading request schema"""

    priority: PriorityLevel = Field("normal", description="Loading priority level")
    force_unload: bool = Field(False, description="Force unload other models if needed")

    model_config = ConfigDict(
        json_schema_extra={"example": {"priority": "normal", "force_unload": False}}
    )


class ModelLoadResponse(BaseModel):
    """Model loading response schema"""

    success: bool = Field(..., description="Whether loading was initiated successfully")
    model_id: str = Field(..., description="Model ID being loaded")
    status: str = Field(..., description="Current loading status")
    estimated_load_time: float | None = Field(
        None, description="Estimated loading time in seconds"
    )
    message: str = Field(..., description="Status message")
    timestamp: float = Field(
        default_factory=time.time, description="Response timestamp"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "model_id": "deepseek-coder-33b-awq",
                "status": "loading",
                "estimated_load_time": 30.5,
                "message": "Model loading started",
                "timestamp": 1703123456.789,
            }
        }
    )


class ModelLoadErrorResponse(BaseModel):
    """Model loading error response schema"""

    success: bool = Field(False, description="Whether loading was successful")
    error: str = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    required_vram_mb: int | None = Field(None, description="Required VRAM in MB")
    available_vram_mb: int | None = Field(None, description="Available VRAM in MB")
    timestamp: float = Field(
        default_factory=time.time, description="Response timestamp"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": False,
                "error": "insufficient_resources",
                "message": "Not enough VRAM to load model. Need 12000MB, have 8000MB available.",
                "required_vram_mb": 12000,
                "available_vram_mb": 8000,
                "timestamp": 1703123456.789,
            }
        }
    )


class ModelUnloadRequest(BaseModel):
    """Model unloading request schema"""

    force: bool = Field(False, description="Force unload even if model is busy")
    wait_for_inference: bool = Field(
        True, description="Wait for current inference to finish"
    )

    model_config = ConfigDict(
        json_schema_extra={"example": {"force": False, "wait_for_inference": True}}
    )


class ModelUnloadResponse(BaseModel):
    """Model unloading response schema"""

    success: bool = Field(
        ..., description="Whether unloading was initiated successfully"
    )
    model_id: str = Field(..., description="Model ID being unloaded")
    status: str = Field(..., description="Current unloading status")
    estimated_unload_time: float | None = Field(
        None, description="Estimated unloading time in seconds"
    )
    message: str = Field(..., description="Status message")
    timestamp: float = Field(
        default_factory=time.time, description="Response timestamp"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "model_id": "deepseek-coder-33b-awq",
                "status": "unloading",
                "estimated_unload_time": 5.2,
                "message": "Model unloading started",
                "timestamp": 1703123456.789,
            }
        }
    )
