"""JSON-RPC 2.0 request/response types and handlers for Universal Protocol."""

from universal_protocol.rpc.client import (
    AsyncRPCClient,
    RPCClient,
)
from universal_protocol.rpc.handlers import (
    handle_cancel_inference,
    handle_count_tokens,
    handle_debug_stats,
    handle_health,
    handle_load_model,
    handle_unload_model,
)
from universal_protocol.rpc.types import (
    CancelInferenceParams,
    CancelInferenceResult,
    CountTokensParams,
    CountTokensResult,
    DebugStatsParams,
    DebugStatsResult,
    HealthParams,
    HealthResult,
    LoadModelParams,
    LoadModelResult,
    UnloadModelParams,
    UnloadModelResult,
)

__all__ = [
    # Types
    "LoadModelParams",
    "LoadModelResult",
    "UnloadModelParams",
    "UnloadModelResult",
    "HealthParams",
    "HealthResult",
    "CountTokensParams",
    "CountTokensResult",
    "CancelInferenceParams",
    "CancelInferenceResult",
    "DebugStatsParams",
    "DebugStatsResult",
    # Handlers
    "handle_load_model",
    "handle_unload_model",
    "handle_health",
    "handle_count_tokens",
    "handle_cancel_inference",
    "handle_debug_stats",
    # Clients
    "RPCClient",
    "AsyncRPCClient",
]
