"""
Process communication modules for worker management.

Modules are split by responsibility:
- manager: High-level orchestration (send_model_config entry point)
- config_builder: Model configuration construction from registry
- rpc_client: RPC socket communication
- health_checks: Health validation and state queries
- cleanup: Worker cleanup and resource management (event-driven)
- error_handling: Error handling and logging utilities
- orchestration: Model loading flow orchestration
- event_handlers: Event handlers for cleanup events

The manager coordinates across modules while maintaining state machine integrity.
Cleanup operations are event-driven using universal_event_bus.
"""

from .cleanup import (
    ResourceStateUpdateRequested,
    SocketCleanupRequested,
    SupervisorTerminationRequested,
    WorkerCleanupRequested,
)
from .event_handlers import register_cleanup_event_handlers
from .manager import ProcessCommunicationManager

__all__ = [
    "ProcessCommunicationManager",
    "WorkerCleanupRequested",
    "ResourceStateUpdateRequested",
    "SocketCleanupRequested",
    "SupervisorTerminationRequested",
    "register_cleanup_event_handlers",
]
