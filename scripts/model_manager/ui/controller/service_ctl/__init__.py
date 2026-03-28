"""Service controller — build, start, stop managed services.

Part of the model_manager UI controller. Re-exports public API for backward
compatibility. Use ServiceController for orchestration; service-specific
start/stop are available as methods on the controller.
"""

from .cloud_proxy_service import start_cloud_proxy, stop_cloud_proxy
from .core import ServiceController
from .event_service import start_event_service, stop_event_service
from .rag_service import start_rag, stop_rag

__all__ = [
    "ServiceController",
    "start_cloud_proxy",
    "start_event_service",
    "start_rag",
    "stop_cloud_proxy",
    "stop_event_service",
    "stop_rag",
]
