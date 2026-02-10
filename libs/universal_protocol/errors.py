"""Error definitions and unified error envelope for Universal Protocol.

Error envelope structure:
{
  "code": "OOM|TIMEOUT|INVALID_MODEL|STREAM_CLOSED|...",
  "message": "Human readable description",
  "source": "rpc|stream|engine|gateway|edge|master",
  "retryable": true,
  "data": {"detail": "extra info"}  # optional
}

Source semantics:
- "rpc": JSON-RPC validation or method handling error
- "stream": WebSocket streaming error (backpressure, socket failure)
- "engine": GPU inference engine error (OOM, CUDA failure, model loading)
- "worker": Worker process error (inference, model state)
- "gateway": Gateway-level error (capacity, model routing)
- "edge": Edge node error (federation, slot reservation)
- "master": Master node error (proxy, orchestration)
"""

from typing import Any, Literal

# Source layers for error origin tracking
type ErrorSource = Literal[
    "rpc", "stream", "engine", "worker", "gateway", "edge", "master"
]


class ProtocolError(Exception):
    """Base exception for protocol-level errors."""

    def __init__(
        self,
        code: str,
        message: str,
        source: ErrorSource,
        retryable: bool = False,
        data: dict[str, Any] | None = None,
    ):
        """Initialize protocol error.

        Args:
            code: Error classification (e.g., "OOM", "TIMEOUT", "INVALID_MODEL")
            message: Human-readable error description
            source: Error origin layer
            retryable: Whether the error is retryable by the caller
            data: Optional extra context dict
        """
        self.code = code
        self.message = message
        self.source = source
        self.retryable = retryable
        self.data = data or {}
        super().__init__(f"[{source}] {code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        """Convert to error envelope dict.

        Returns:
            Dict with code, message, source, retryable, and data (if non-empty)
        """
        return {
            "code": self.code,
            "message": self.message,
            "source": self.source,
            "retryable": self.retryable,
            "data": self.data,
        }


class RPCError(ProtocolError):
    """Error in JSON-RPC request/response layer.

    Examples: Invalid request format, unknown method, parameter validation.
    """

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        data: dict[str, Any] | None = None,
    ):
        """Initialize RPC error.

        Args:
            code: Error code (e.g., "INVALID_REQUEST", "METHOD_NOT_FOUND")
            message: Human-readable description
            retryable: Whether the error is retryable
            data: Optional extra context
        """
        super().__init__(
            code=code, message=message, source="rpc", retryable=retryable, data=data
        )


class StreamError(ProtocolError):
    """Error in WebSocket streaming layer.

    Examples: Backpressure timeout, socket closed, invalid stream ID.
    """

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        data: dict[str, Any] | None = None,
    ):
        """Initialize stream error.

        Args:
            code: Error code (e.g., "STREAM_CLOSED", "QUEUE_TIMEOUT")
            message: Human-readable description
            retryable: Whether the error is retryable
            data: Optional extra context
        """
        super().__init__(
            code=code, message=message, source="stream", retryable=retryable, data=data
        )


class EngineError(ProtocolError):
    """Error in GPU inference engine.

    Examples: Out of memory, CUDA failure, model loading error.
    """

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        data: dict[str, Any] | None = None,
    ):
        """Initialize engine error.

        Args:
            code: Error code (e.g., "OOM", "MODEL_NOT_FOUND", "CUDA_ERROR")
            message: Human-readable description
            retryable: Whether the error is retryable
            data: Optional extra context
        """
        super().__init__(
            code=code, message=message, source="engine", retryable=retryable, data=data
        )


def error_envelope(
    code: str,
    message: str,
    source: Literal["rpc", "stream", "engine", "gateway", "edge", "master"],
    retryable: bool = False,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a unified error envelope dict.

    Convenience function for creating error responses without exception classes.

    Args:
        code: Error classification
        message: Human-readable description
        source: Error origin layer
        retryable: Whether the error is retryable by the caller
        data: Optional extra context

    Returns:
        Dict with code, message, source, retryable, and data (if non-empty)

    Example:
        >>> error_envelope("OOM", "CUDA out of memory", "engine")
        {'code': 'OOM', 'message': 'CUDA out of memory',
         'source': 'engine', 'retryable': False}

        >>> error_envelope(
        ...     "STICKY_CAPACITY", "Model at capacity", "edge",
        ...     retryable=True, data={"timeout_ms": 500}
        ... )
        {'code': 'STICKY_CAPACITY', 'message': 'Model at capacity',
         'source': 'edge', 'retryable': True, 'data': {'timeout_ms': 500}}
    """
    return {
        "code": code,
        "message": message,
        "source": source,
        "retryable": retryable,
        "data": data or {},
    }
