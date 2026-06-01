"""
Simple process manager using process_ipc directly.

This module provides a simple wrapper around process_ipc.UnixSocketProcessManager
for managing LLM worker processes without complex abstractions.
"""

try:
    import psutil
except ImportError:
    psutil = None

# Structured error codes for stream termination
class StreamErrorCode:
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    ENGINE_ERROR = "engine_error"
    TRANSPORT_ERROR = "transport_error"


from universal_logging import get_logger  # noqa: E402

logger = get_logger(__name__)

if psutil is None:
    logger.warning("psutil not available - process cleanup will be limited")

# Import enhanced error handling

# Import worker utilities and exceptions

# Import extracted process managers
# Health observer removed - using pure event-driven detection

# Create structured logger for process events using universal_logging directly
structured_logger = get_logger("universal_llm_gateway.workers")


# ProcessManager class removed in Phase 3 - functionality moved to WorkerController
