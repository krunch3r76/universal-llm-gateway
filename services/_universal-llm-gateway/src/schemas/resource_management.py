"""Queue management API response schemas for Universal LLM Gateway

This module provides Pydantic schemas for the enhanced request queue management
API endpoints including resource status, model management, and priority handling.
"""

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Model Status Enums
ModelStatus = Literal["not_loaded", "loading", "loaded", "busy", "unloading", "error"]
PriorityLevel = Literal["high", "normal", "low"]


# Resource Status Schemas
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


# Model Status Schemas
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


# Model Loading/Unloading Schemas
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


# Model Requirements Schema
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


# Error Codes
class ErrorCodes:
    """Standard error codes for queue management API"""

    INSUFFICIENT_RESOURCES = "insufficient_resources"
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_ALREADY_LOADED = "model_already_loaded"
    MODEL_NOT_LOADED = "model_not_loaded"
    MODEL_BUSY = "model_busy"
    LOAD_FAILED = "load_failed"
    UNLOAD_FAILED = "unload_failed"
    INVALID_PRIORITY = "invalid_priority"
    RESOURCE_TRACKING_ERROR = "resource_tracking_error"
    INFERENCE_STATE_ERROR = "inference_state_error"
