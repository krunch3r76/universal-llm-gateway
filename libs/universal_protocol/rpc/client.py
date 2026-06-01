"""JSON-RPC 2.0 client for Unix socket communication.

Provides RPCClient for making JSON-RPC 2.0 calls to a worker over a Unix socket.
Uses httpx with Unix socket transport for HTTP/1.1 over UDS.

Example:
    >>> client = RPCClient(socket_path="/tmp/universal-protocol/worker-1.sock")
    >>> result = await client.call_rpc("health", {})
    >>> print(result)
    {"status": "ready", "models": ["llama-3.2"]}
"""

import asyncio
import json
import time
from typing import Any

import httpx
from universal_logging import get_logger

from universal_protocol.errors import RPCError
from universal_protocol.ids import generate_request_id
from universal_protocol.observability import get_metrics_instance

logger = get_logger(__name__)


class RPCClient:
    """JSON-RPC 2.0 client for Unix socket communication.

    Implements JSON-RPC 2.0 over HTTP/1.1 on Unix domain sockets.
    Handles request/response envelope formatting, error parsing, and cleanup.
    """

    def __init__(
        self,
        socket_path: str,
        timeout: float = 30.0,
        verify_socket: bool = True,
    ):
        """Initialize RPC client.

        Args:
            socket_path: Path to Unix socket (e.g.,
                "/tmp/universal-protocol/worker-1.sock")
            timeout: Request timeout in seconds (default: 30.0)
            verify_socket: If True, check socket exists before first use

        Raises:
            FileNotFoundError: If verify_socket=True and socket doesn't exist
            ValueError: If socket_path is invalid
        """
        if not socket_path or not isinstance(socket_path, str):
            raise ValueError(f"socket_path must be non-empty string, got {socket_path}")

        self.socket_path = socket_path
        self.timeout = timeout
        self.base_url = f"http+unix://{socket_path.replace('/', '%2F')}"

        # Verify socket exists if requested
        if verify_socket:
            import os

            if not os.path.exists(socket_path):
                raise FileNotFoundError(f"Socket not found: {socket_path}")

        # Create httpx client with Unix socket transport
        # The transport will be reused for all requests
        self._transport = httpx.HTTPTransport(uds=socket_path)
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        """Get or create httpx client (lazy initialization).

        Returns:
            httpx.Client configured for Unix socket communication
        """
        if self._client is None:
            self._client = httpx.Client(
                transport=self._transport,
                timeout=self.timeout,
            )
        return self._client

    async def call_rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Make a JSON-RPC 2.0 call to the worker.

        Args:
            method: RPC method name (e.g., "health", "start_inference")
            params: Method parameters (dict or None for methods with no params)
            request_id: Optional request ID (generated if not provided)
            correlation_id: Optional correlation ID for request tracing

        Returns:
            Result dict from RPC method (the "result" field of response)

        Raises:
            RPCError: If RPC returns an error response
            httpx.RequestError: If network communication fails
            ValueError: If response format is invalid

        Example:
            >>> result = await client.call_rpc("health")
            >>> print(result["status"])
            "ready"

            >>> result = await client.call_rpc(
            ...     "load_model",
            ...     params={
            ...         "name": "llama-3.2",
            ...         "path": "/models/llama-3.2",
            ...     }
            ... )
            >>> print(result["context_size"])
            4096
        """
        if not method or not isinstance(method, str):
            raise ValueError(f"method must be non-empty string, got {method}")

        # Generate request ID if not provided
        if request_id is None:
            request_id = generate_request_id()

        # Add correlation ID to params if provided
        if params is None:
            params = {}
        if correlation_id:
            params["correlation_id"] = correlation_id

        # Log RPC call with correlation ID and request_id
        log_prefix = (
            f"[{correlation_id}]" if correlation_id else f"[request_id={request_id}]"
        )
        logger.info(f"{log_prefix} RPC call: {method} to {self.socket_path}")
        if params:
            logger.debug(f"{log_prefix} RPC params: {params}")

        # Build JSON-RPC 2.0 request
        request_body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": request_id,
        }
        if params is not None:
            request_body["params"] = params

        # Make HTTP POST request with timing
        start_time = time.time()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._get_client().post(
                    "http://localhost/rpc",  # UDS: only socket matters
                    json=request_body,
                ),
            )
            # Record successful RPC call metrics
            latency = time.time() - start_time
            metrics = get_metrics_instance()
            # Note: RPC request counter is incremented on server side to avoid
            # double-counting
            metrics.record_rpc_latency(method, latency)
        except Exception as e:
            # Record error metrics
            latency = time.time() - start_time
            metrics = get_metrics_instance()
            metrics.increment_rpc_error("COMMUNICATION_ERROR")
            metrics.record_rpc_latency(method, latency)
            logger.error(f"[request_id={request_id}] RPC communication failed: {e}")
            raise httpx.RequestError(
                f"Failed to communicate with worker at {self.socket_path}: {e}"
            ) from e

        # Parse response
        try:
            response_body = response.json()
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid JSON in RPC response: {response.text[:200]}"
            ) from e

        # Validate JSON-RPC response structure
        if not isinstance(response_body, dict):
            raise ValueError(f"RPC response must be a dict, got {type(response_body)}")

        # Check for error response
        if "error" in response_body:
            error_data = response_body.get("error", {})
            error_code = error_data.get("code", "UNKNOWN_ERROR")
            error_message = error_data.get("message", "Unknown error")
            error_detail = error_data.get("data", {})

            # Extract protocol error info if available
            protocol_code = error_detail.get("code", error_code)
            protocol_source = error_detail.get("source", "rpc")
            protocol_message = error_detail.get("message", error_message)

            logger.error(
                f"{log_prefix} RPC error: {method} failed with "
                f"code={protocol_code}, message={protocol_message}, "
                f"source={protocol_source}"
            )

            raise RPCError(
                code=protocol_code,
                message=protocol_message,
                data=error_detail,
            )

        # Validate response ID matches request ID (JSON-RPC 2.0 requirement)
        response_id = response_body.get("id")
        if response_id != request_id:
            raise ValueError(
                f"RPC response ID mismatch: expected {request_id}, got {response_id}. "
                "This indicates a response routing error - responses may be "
                "getting mixed up between concurrent requests."
            )

        # Extract result
        if "result" not in response_body:
            raise ValueError(f"RPC response missing 'result' field: {response_body}")

        result = response_body["result"]
        logger.info(f"{log_prefix} RPC response received for {method}")
        logger.debug(f"{log_prefix} RPC result: {result}")

        return result

    async def health(self) -> dict[str, Any]:
        """
        Convenience method to call the health RPC method.

        Returns:
            Dict containing health status with keys:
            - status: "ready" | "busy" | "error"
            - models: List of loaded model names

        Raises:
            RPCError: If health check fails

        Example:
            >>> health_info = await client.health()
            >>> print(health_info["status"])
            "ready"
        """
        return await self.call_rpc("health")

    async def call(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Convenience method that aliases call_rpc for cleaner API.

        This allows users to write client.call() instead of client.call_rpc()
        matching the documented API examples in the MVP spec.

        Args:
            method: RPC method name
            params: Method parameters (optional)

        Returns:
            Result dict from RPC method

        Raises:
            Same as call_rpc()

        Example:
            >>> result = await client.call("load_model", {
            ...     "name": "llama-3.2",
            ...     "path": "/models/llama-3.2"
            ... })
        """
        return await self.call_rpc(method, params)

    async def list_models(self) -> list[str]:
        """
        Convenience method to get list of loaded models.

        Returns:
            List of model names currently loaded

        Raises:
            RPCError: If health check fails

        Example:
            >>> models = await client.list_models()
            >>> print(models)
            ["llama-3.2", "mistral-7b"]
        """
        health_info = await self.health()
        return health_info.get("models", [])

    async def call_rpc_async(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Async version of call_rpc (uses same httpx client).

        Args:
            method: RPC method name
            params: Method parameters
            request_id: Optional request ID

        Returns:
            Result dict from RPC method

        Raises:
            Same as call_rpc()

        Note:
            This method exists for API compatibility. For true async I/O,
            use AsyncRPCClient instead.
        """
        return await self.call_rpc(method, params, request_id)

    def close(self) -> None:
        """Close the RPC client and release resources.

        Should be called when the client is no longer needed to ensure
        proper socket cleanup.
        """
        if self._client is not None:
            self._client.close()
            self._client = None

    async def __aenter__(self) -> "RPCClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        self.close()

    def __enter__(self) -> "RPCClient":
        """Sync context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Sync context manager exit."""
        self.close()


class AsyncRPCClient:
    """Async JSON-RPC 2.0 client using httpx AsyncClient.

    For fully async workflows, use this client instead of RPCClient.
    Provides true async/await semantics with httpx.AsyncClient.

    Example:
        >>> async with AsyncRPCClient(socket_path) as client:
        ...     result = await client.call_rpc("health")
    """

    def __init__(
        self,
        socket_path: str,
        timeout: float = 30.0,
        verify_socket: bool = True,
    ):
        """Initialize async RPC client.

        Args:
            socket_path: Path to Unix socket
            timeout: Request timeout in seconds
            verify_socket: If True, check socket exists before first use

        Raises:
            FileNotFoundError: If verify_socket=True and socket doesn't exist
            ValueError: If socket_path is invalid
        """
        if not socket_path or not isinstance(socket_path, str):
            raise ValueError(f"socket_path must be non-empty string, got {socket_path}")

        self.socket_path = socket_path
        self.timeout = timeout

        # Verify socket exists if requested
        if verify_socket:
            import os

            if not os.path.exists(socket_path):
                raise FileNotFoundError(f"Socket not found: {socket_path}")

        # Create async transport
        self._transport = httpx.AsyncHTTPTransport(uds=socket_path)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async httpx client (lazy initialization).

        Returns:
            httpx.AsyncClient configured for Unix socket communication
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=self._transport,
                timeout=self.timeout,
            )
        return self._client

    async def call_rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Make an async JSON-RPC 2.0 call to the worker.

        Args:
            method: RPC method name
            params: Method parameters
            request_id: Optional request ID (generated if not provided)
            correlation_id: Optional correlation ID for request tracing

        Returns:
            Result dict from RPC method

        Raises:
            RPCError: If RPC returns an error response
            httpx.RequestError: If network communication fails
            ValueError: If response format is invalid
        """
        if not method or not isinstance(method, str):
            raise ValueError(f"method must be non-empty string, got {method}")

        # Generate request ID if not provided
        if request_id is None:
            request_id = generate_request_id()

        # Add correlation ID to params if provided
        if params is None:
            params = {}
        if correlation_id:
            params["correlation_id"] = correlation_id

        # Log RPC call with correlation ID and request_id
        log_prefix = (
            f"[{correlation_id}]" if correlation_id else f"[request_id={request_id}]"
        )
        logger.info(f"{log_prefix} Async RPC call: {method} to {self.socket_path}")
        if params:
            logger.debug(f"{log_prefix} Async RPC params: {params}")

        # Build JSON-RPC 2.0 request
        request_body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": request_id,
        }
        if params is not None:
            request_body["params"] = params

        # Make HTTP POST request
        client = await self._get_client()
        try:
            response = await client.post(
                "http://localhost/rpc",
                json=request_body,
            )
        except Exception as e:
            logger.error(f"{log_prefix} RPC communication failed: {e}")
            raise httpx.RequestError(
                f"Failed to communicate with worker at {self.socket_path}: {e}"
            ) from e

        # Parse response
        try:
            response_body = response.json()
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid JSON in RPC response: {response.text[:200]}"
            ) from e

        # Validate JSON-RPC response structure
        if not isinstance(response_body, dict):
            raise ValueError(f"RPC response must be a dict, got {type(response_body)}")

        # Check for error response
        if "error" in response_body:
            error_data = response_body.get("error", {})
            error_code = error_data.get("code", "UNKNOWN_ERROR")
            error_message = error_data.get("message", "Unknown error")
            error_detail = error_data.get("data", {})

            # Extract protocol error info if available
            protocol_code = error_detail.get("code", error_code)
            protocol_source = error_detail.get("source", "rpc")
            protocol_message = error_detail.get("message", error_message)

            logger.error(
                f"{log_prefix} RPC error: {method} failed with "
                f"code={protocol_code}, message={protocol_message}, "
                f"source={protocol_source}"
            )

            raise RPCError(
                code=protocol_code,
                message=protocol_message,
                data=error_detail,
            )

        # Validate response ID matches request ID (JSON-RPC 2.0 requirement)
        response_id = response_body.get("id")
        if response_id != request_id:
            raise ValueError(
                f"RPC response ID mismatch: expected {request_id}, got {response_id}. "
                "This indicates a response routing error - responses may be "
                "getting mixed up between concurrent requests."
            )

        # Extract result
        if "result" not in response_body:
            raise ValueError(f"RPC response missing 'result' field: {response_body}")

        result = response_body["result"]
        logger.info(f"{log_prefix} RPC response received for {method}")
        logger.debug(f"{log_prefix} RPC result: {result}")

        return result

    async def health(self) -> dict[str, Any]:
        """
        Convenience method to call the health RPC method.

        Returns:
            Dict containing health status with keys:
            - status: "ready" | "busy" | "error"
            - models: List of loaded model names

        Raises:
            RPCError: If health check fails

        Example:
            >>> health_info = await client.health()
            >>> print(health_info["status"])
            "ready"
        """
        return await self.call_rpc("health")

    async def call(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Convenience method that aliases call_rpc for cleaner API.

        This allows users to write client.call() instead of client.call_rpc()
        matching the documented API examples in the MVP spec.

        Args:
            method: RPC method name
            params: Method parameters (optional)

        Returns:
            Result dict from RPC method

        Raises:
            Same as call_rpc()

        Example:
            >>> result = await client.call("load_model", {
            ...     "name": "llama-3.2",
            ...     "path": "/models/llama-3.2"
            ... })
        """
        return await self.call_rpc(method, params)

    async def list_models(self) -> list[str]:
        """
        Convenience method to get list of loaded models.

        Returns:
            List of model names currently loaded

        Raises:
            RPCError: If health check fails

        Example:
            >>> models = await client.list_models()
            >>> print(models)
            ["llama-3.2", "mistral-7b"]
        """
        health_info = await self.health()
        return health_info.get("models", [])

    async def start_inference(
        self,
        payload: dict[str, Any] | None = None,
        prompt: str | None = None,
        messages: list[dict[str, str]] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Convenience method to start an inference stream.

        Supports multiple calling patterns for ergonomics:

        1. Dict payload: client.start_inference({"prompt": "...", "max_tokens": 100})
        2. Keyword args: client.start_inference(prompt="...", max_tokens=100)
        3. Mixed: client.start_inference({"prompt": "..."}, temperature=0.7)

        Args:
            payload: Optional dict containing inference parameters.
                     If provided, overrides individual prompt/messages/kwargs arguments.
                     Can contain "prompt","
                         ""messages", and any other inference parameters.
            prompt: Text prompt for completion (mutually exclusive with messages)
            messages: Chat messages for conversation (mutually exclusive with prompt)
            **kwargs: Additional inference parameters (max_tokens, temperature, etc.)

        Returns:
            Dict containing:
            - stream_id: Unique stream identifier
            - websocket_path: Path to connect WebSocket for streaming

        Raises:
            ValueError: If neither prompt nor messages provided, or both provided
            RPCError: If inference start fails

        Example:
            >>> # Dict payload pattern
            >>> result = await client.start_inference({
            ...     "prompt": "Once upon a time",
            ...     "max_tokens": 100,
            ...     "temperature": 0.7
            ... })

            >>> # Keyword arguments pattern
            >>> result = await client.start_inference(
            ...     prompt="Once upon a time",
            ...     max_tokens=100,
            ...     temperature=0.7
            ... )

            >>> # Chat messages
            >>> result = await client.start_inference(
            ...     messages=[{"role": "user", "content": "Hello!"}],
            ...     max_tokens=50
            ... )
        """
        # If payload dict is provided, use it directly (with kwargs override)
        if payload is not None:
            if not isinstance(payload, dict):
                raise ValueError(f"payload must be a dict, got {type(payload)}")

            # Use payload as base and allow kwargs to override
            params = payload.copy()
            params.update(kwargs)
        else:
            # Build from individual arguments
            if prompt is None and messages is None:
                raise ValueError("Either 'prompt' or 'messages' must be provided")
            if prompt is not None and messages is not None:
                raise ValueError("Cannot provide both 'prompt' and 'messages'")

            # Build parameters
            params = dict(kwargs)
            if prompt is not None:
                params["prompt"] = prompt
            else:
                params["messages"] = messages

        return await self.call_rpc("start_inference", params)

    async def close(self) -> None:
        """Close the RPC client and release resources."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "AsyncRPCClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()
