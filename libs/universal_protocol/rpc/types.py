"""TypedDict definitions for JSON-RPC 2.0 request/response schemas.

Defines parameter and result types for 6 RPC methods:
1. load_model
2. unload_model
3. health
4. count_tokens
5. cancel_inference
6. debug_stats

Note: start_inference types are NOT defined here. Workers implement their own
handlers with engine-specific parameter validation and response schemas.
"""

from typing import Any, TypedDict

try:
    from typing import NotRequired
except ImportError:
    from typing import NotRequired


# ============================================================================
# load_model
# ============================================================================


class LoaderConfig(TypedDict, total=False):
    """Loader-specific configuration (optional fields).

    Example:
        {"max_tokens": 4096, "gpu_layers": 35}
    """

    max_tokens: int
    gpu_layers: int
    # Other loader-specific params may be added


class LoadModelParams(TypedDict):
    """Parameters for load_model RPC method.

    Fields:
        name: Model identifier (e.g., "llama-3.2")
        path: File system path to model (e.g., "/models/llama-3.2")
        loader_config: Optional loader-specific config (max_tokens, gpu_layers, etc.)
    """

    name: str
    path: str
    loader_config: NotRequired[LoaderConfig]


class LoadModelResult(TypedDict):
    """Response from load_model RPC method.

    Fields:
        success: Whether model loaded successfully
        model_loaded: True if model is now in memory
        context_size: Maximum context window (tokens)
    """

    success: bool
    model_loaded: bool
    context_size: int


# ============================================================================
# unload_model
# ============================================================================


class UnloadModelParams(TypedDict):
    """Parameters for unload_model RPC method.

    Fields:
        name: Model identifier to unload
    """

    name: str


class UnloadModelResult(TypedDict):
    """Response from unload_model RPC method.

    Fields:
        success: Whether unload completed
    """

    success: bool


# ============================================================================
# health
# ============================================================================


class HealthParams(TypedDict):
    """Parameters for health RPC method.

    No parameters required.
    """

    pass


class HealthResult(TypedDict):
    """Response from health RPC method.

    Fields:
        status: Worker status ("ready" | "busy" | "error")
        models: List of currently loaded model names
    """

    status: str  # "ready" | "busy" | "error"
    models: list[str]


# ============================================================================
# count_tokens
# ============================================================================


class CountTokensParams(TypedDict):
    """Parameters for count_tokens RPC method.

    Fields:
        text: Input text to tokenize
    """

    text: str


class CountTokensResult(TypedDict):
    """Response from count_tokens RPC method.

    Fields:
        count: Number of tokens in text
    """

    count: int


# ============================================================================
# cancel_inference
# ============================================================================


class CancelInferenceParams(TypedDict):
    """Parameters for cancel_inference RPC method.

    Fields:
        stream_id: ID of stream to cancel
    """

    stream_id: str


class CancelInferenceResult(TypedDict):
    """Response from cancel_inference RPC method.

    Fields:
        success: Whether cancellation was successful
    """

    success: bool


# ============================================================================
# debug_stats
# ============================================================================


class DebugStatsParams(TypedDict):
    """Parameters for debug_stats RPC method.

    No parameters required.
    """

    pass


class DebugStatsResult(TypedDict):
    """Response from debug_stats RPC method.

    Fields:
        fds_open: Number of open file descriptors
        tasks_running: Number of concurrent asyncio tasks
    """

    fds_open: int
    tasks_running: int


# ============================================================================
# JSON-RPC 2.0 Request/Response Envelopes
# ============================================================================


class JSONRPCRequest(TypedDict, total=False):
    """Standard JSON-RPC 2.0 request envelope.

    Fields:
        jsonrpc: Must be "2.0"
        method: RPC method name
        params: Method parameters (optional for methods with no params)
        id: Request ID (optional, but required for responses)
    """

    jsonrpc: str
    method: str
    params: Any | None
    id: str


class JSONRPCResponse(TypedDict, total=False):
    """Standard JSON-RPC 2.0 response envelope.

    Either 'result' or 'error' is present, never both.

    Fields:
        jsonrpc: Must be "2.0"
        result: Method result (if successful)
        error: Error object (if failed)
        id: Request ID (echo from request)
    """

    jsonrpc: str
    result: Any | None
    error: dict[str, Any] | None
    id: str
