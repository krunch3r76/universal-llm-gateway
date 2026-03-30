"""Worker module - process lifecycle and RPC handling.

Provides the Worker class for model inference workers.
"""

from typing import Any, override

from .deadline import enforce_deadline, enforce_idle_timeout
from .engine_lifecycle import EngineLifecycle
from .process import Worker as WorkerBase
from .rpc import (
    EmbeddingHandlers,
    FluxImageHandlers,
    InferenceHandlers,
    LifecycleHandlers,
    LoadHandlers,
    MetadataHandlers,
    RerankHandlers,
    StreamHandlers,
)
from .rpc.helpers import RPCHelpers
from .rpc_helpers import RPCHelpers as LegacyRPCHelpers
from .stream import StreamingHandlers


# Compose final Worker class with all mix-ins
class Worker(
    WorkerBase,
    LoadHandlers,
    InferenceHandlers,
    LifecycleHandlers,
    StreamHandlers,
    MetadataHandlers,
    RPCHelpers,
    LegacyRPCHelpers,
    StreamingHandlers,
    FluxImageHandlers,
    EmbeddingHandlers,
    RerankHandlers,
    EngineLifecycle,
):
    """
    Worker with integrated RPC handlers via multiple inheritance.

    Handler resolution order (MRO):
    1. LoadHandlers - Model lifecycle
    2. InferenceHandlers - Inference execution
    3. LifecycleHandlers - Worker init/health
    4. StreamHandlers - Stream management
    5. MetadataHandlers - Metadata queries
    6. RPCHelpers - Shared utilities (new)
    7. LegacyRPCHelpers - Legacy utilities (rpc_helpers.py)
    8. StreamingHandlers - Stream start logic
    9. FluxImageHandlers - Flux.2 image generation
    10. EmbeddingHandlers - Text embedding generation
    11. RerankHandlers - Cross-encoder reranking
    12. EngineLifecycle - Engine loading
    13. WorkerBase - Base process infrastructure
    """

    @override
    async def process_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """
        Process command - delegates to EngineLifecycle implementation.

        This explicit override ensures the ABC requirement is satisfied
        and directs to the correct implementation via MRO.

        Inputs:
            command: dict containing "command_type" (str) and handler-specific params

        Outputs:
            dict: JSON-serializable result from the command handler

        Raises:
            ValueError: if command_type is missing or unknown
        """
        # Call EngineLifecycle's implementation via MRO
        return await EngineLifecycle.process_command(self, command)


__all__ = ["Worker", "enforce_deadline", "enforce_idle_timeout"]
