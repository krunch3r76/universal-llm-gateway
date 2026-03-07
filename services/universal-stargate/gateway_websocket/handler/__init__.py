"""WebSocket message handlers - per-domain dispatch."""

from .base import AsyncMessageHandler, MessageHandler, SyncMessageHandler
from .catalog import CatalogUpdateHandler
from .context import HandlerContext
from .heartbeat import TelemetryHeartbeatHandler
from .model_availability import ModelIdleHandler, ModelUnloadedHandler
from .model_loading import (
    ModelBusyHandler,
    ModelLoadedHandler,
    ModelLoadFailedHandler,
    ModelLoadingStartedHandler,
)
from .query import QueryResponseHandler
from .registry import HandlerRegistry
from .request_inference import RequestInferenceStartedHandler
from .system import (
    ErrorHandler,
    GatewayDrainingHandler,
    GatewayShutdownHandler,
    PingHandler,
    ResourceUpdateHandler,
)
from .telemetry import (
    ComputeCapacityTelemetryHandler,
    ComputeQueueAcquiredHandler,
    ComputeQueueWaitHandler,
)

__all__ = [
    "MessageHandler",
    "SyncMessageHandler",
    "AsyncMessageHandler",
    "HandlerContext",
    "HandlerRegistry",
    "ModelLoadingStartedHandler",
    "ModelLoadedHandler",
    "ModelLoadFailedHandler",
    "ModelBusyHandler",
    "ModelIdleHandler",
    "ModelUnloadedHandler",
    "PingHandler",
    "ResourceUpdateHandler",
    "ErrorHandler",
    "GatewayShutdownHandler",
    "GatewayDrainingHandler",
    "CatalogUpdateHandler",
    "QueryResponseHandler",
    "TelemetryHeartbeatHandler",
    "RequestInferenceStartedHandler",
    "ComputeCapacityTelemetryHandler",
    "ComputeQueueWaitHandler",
    "ComputeQueueAcquiredHandler",
]
