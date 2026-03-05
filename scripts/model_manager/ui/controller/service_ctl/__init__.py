"""Service controller — build, start, stop Gateway, Stargate, RAG, Cloud Proxy, sidecar.

Part of the model_manager UI controller. Re-exports public API for backward
compatibility. Use ServiceController for orchestration; start_rag/stop_rag and
start_cloud_proxy/stop_cloud_proxy are available as methods on the controller.
"""

from .cloud_proxy_service import start_cloud_proxy, stop_cloud_proxy
from .core import ServiceController
from .rag_service import start_rag, stop_rag

__all__ = [
    "ServiceController",
    "start_rag",
    "stop_rag",
    "start_cloud_proxy",
    "stop_cloud_proxy",
]
