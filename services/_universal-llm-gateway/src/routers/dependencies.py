"""Centralized dependency injection functions for the Universal LLM Gateway API."""

from fastapi import HTTPException, Request

from ..core.events import EventBus
from ..core.gateway_config import GatewayConfig
from ..core.hot_reload import HotReloadManager
from ..core.model_registry import ModelRegistry
from ..core.workers import WorkerController
from .model_metadata_adapter import ModelMetadataAdapter


def get_model_registry(request: Request) -> ModelRegistry:
    """Dependency to get model registry from app state."""
    app = request.app
    reg = getattr(app.state, "model_registry", None)
    if reg is None:
        raise HTTPException(status_code=500, detail="Model registry not initialized")
    return reg


def get_model_metadata_adapter(request: Request) -> ModelMetadataAdapter:
    """Dependency to get model metadata adapter from app state."""
    app = request.app
    adapter = getattr(app.state, "model_metadata_adapter", None)
    if adapter is None:
        raise HTTPException(
            status_code=500, detail="Model metadata adapter not initialized"
        )
    return adapter


def get_worker_controller(request: Request) -> WorkerController:
    """
    Dependency to get worker controller from app state.

    This function provides access to the WorkerController which has integrated resource tracking:
    - Model loading/unloading with resource monitoring
    - Inference tracking (marking models as busy/idle)
    - VRAM/RAM usage tracking per model
    - System resource availability monitoring

    This enables the /api/v1/status/resources endpoint to accurately report
    model status and resource usage.
    """
    app = request.app
    manager = getattr(app.state, "worker_controller", None)
    if manager is None:
        raise HTTPException(status_code=500, detail="Worker controller not initialized")
    return manager


def get_gateway_config(request: Request) -> GatewayConfig:
    """Dependency to get gateway configuration from app state."""
    app = request.app
    config = getattr(app.state, "gateway_config", None)
    if config is None:
        raise HTTPException(
            status_code=500, detail="Gateway configuration not initialized"
        )
    return config


def get_event_bus(request: Request) -> EventBus:
    """
    Dependency to get event bus from app state.

    The EventBus provides event-driven architecture support for:
    - Model lifecycle events (loading, unloading)
    - Inference events (started, completed, failed)
    - System resource events (VRAM/RAM updates)

    Subscribers can listen to these events for monitoring, logging, and coordination.
    """
    app = request.app
    event_bus = getattr(app.state, "event_bus", None)
    if event_bus is None:
        raise HTTPException(status_code=500, detail="Event bus not initialized")
    return event_bus


def get_hot_reload_manager(request: Request) -> HotReloadManager:
    """
    Dependency to get hot reload manager from app state.

    The HotReloadManager provides automatic configuration reload functionality:
    - File system monitoring for YAML/JSON changes
    - Debounced reload operations
    - Deep merge of configuration changes
    - Error handling and recovery
    - Status monitoring and metrics
    """
    app = request.app
    hot_reload_manager = getattr(app.state, "hot_reload_manager", None)
    if hot_reload_manager is None:
        raise HTTPException(
            status_code=500, detail="Hot reload manager not initialized"
        )
    return hot_reload_manager
