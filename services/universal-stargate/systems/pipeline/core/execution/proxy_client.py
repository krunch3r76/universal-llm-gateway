"""HTTP client for pipeline → Stargate internal communication."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class ProxyClientConfig:
    """Configuration for ProxyClient transport."""

    # TCP mode
    host: str = "localhost"
    port: int = 9999

    # Unix socket mode (overrides TCP if set)
    unix_socket_path: str | None = None

    # Timeouts
    connect_timeout: float = 5.0
    request_timeout: float = (
        300.0  # Match stargate_config.yaml → request_queue.default_timeout
    )

    @classmethod
    def from_environment(cls) -> ProxyClientConfig:
        """
        Create config from environment variables.

        Detects transport mode:
        - STARGATE_UNIX_SOCKET → Unix socket mode
        - STARGATE_HOST/STARGATE_PORT → TCP mode (default)
        """
        unix_socket = os.environ.get("STARGATE_UNIX_SOCKET")
        if unix_socket:
            return cls(unix_socket_path=unix_socket)

        host = os.environ.get("STARGATE_HOST", "localhost")
        port = int(os.environ.get("STARGATE_PORT", "9999"))
        return cls(host=host, port=port)


class ProxyClientError(Exception):
    """Error from ProxyClient operations."""

    def __init__(
        self, message: str, status_code: int | None = None, detail: Any = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class ProxyClient:
    """
    HTTP client for pipeline → Stargate internal communication.

    Submits requests through Stargate's full pipeline:
    - Transformations (generation_params, message transforms)
    - Profiles (model-specific defaults)
    - Token management
    - Request queue (wait for capacity)
    - Routing (gateway selection, model loading)

    Supports both TCP and Unix socket transports.

    Usage:
        client = ProxyClient.from_environment()
        response, map_req_id, snap_id = await client.chat_completion(
            model="model-id",
            messages=[{"role": "user", "content": "Hello"}],
            execution_id="pipeline-123",
            step_id="step-1",
        )
    """

    def __init__(self, config: ProxyClientConfig | None = None):
        """
        Initialize ProxyClient with configuration.

        Args:
            config: Transport configuration. If None, auto-detect from environment.
        """
        self._config = config or ProxyClientConfig.from_environment()
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def from_environment(cls) -> ProxyClient:
        """Create ProxyClient with transport auto-detected from environment."""
        return cls(ProxyClientConfig.from_environment())

    async def __aenter__(self) -> ProxyClient:
        """Context manager entry - create HTTP client."""
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - close HTTP client."""
        await self.close()

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure HTTP client is initialized."""
        if self._client is None:
            timeout = httpx.Timeout(
                connect=self._config.connect_timeout,
                read=self._config.request_timeout,
                write=self._config.request_timeout,
                pool=self._config.connect_timeout,
            )

            if self._config.unix_socket_path:
                # Unix socket transport
                transport = httpx.AsyncHTTPTransport(uds=self._config.unix_socket_path)
                self._client = httpx.AsyncClient(
                    transport=transport,
                    base_url="http://localhost",  # Host ignored for UDS
                    timeout=timeout,
                )
                logger.debug(
                    f"ProxyClient using Unix socket: {self._config.unix_socket_path}"
                )
            else:
                # TCP transport
                base_url = f"http://{self._config.host}:{self._config.port}"
                self._client = httpx.AsyncClient(
                    base_url=base_url,
                    timeout=timeout,
                )
                logger.debug(f"ProxyClient using TCP: {base_url}")

        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _build_request_headers(
        self,
        execution_id: str | None,
        step_id: str | None,
        skip_token_counting: bool,
        timeout: float | None = None,
        map_iteration_request_id: str | None = None,
    ) -> dict[str, str]:
        """
        Build internal request headers for pipeline identification.

        Args:
            execution_id: Pipeline execution ID (for tracing)
            step_id: Pipeline step ID (for tracing)
            skip_token_counting: Whether to skip token counting
            timeout: Request timeout (passed to stargate for federation)
            map_iteration_request_id: Per-iteration request ID for cancellation tracking

        Returns:
            Headers dict for internal requests
        """
        headers: dict[str, str] = {"X-Pipeline-Internal": "true"}

        if execution_id:
            headers["X-Pipeline-Execution-Id"] = execution_id
        if step_id:
            headers["X-Pipeline-Step-Id"] = step_id
        if skip_token_counting:
            headers["X-Skip-Token-Counting"] = "true"
        if timeout:
            headers["X-Request-Timeout"] = str(timeout)
        if map_iteration_request_id:
            headers["X-Internal-Request-ID"] = map_iteration_request_id

        return headers

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        execution_id: str | None = None,
        step_id: str | None = None,
        skip_token_counting: bool = False,
        timeout: float | None = None,
        map_iteration_request_id: str | None = None,
        **params: Any,
    ) -> tuple[dict[str, Any], str, str]:
        """
        Execute chat completion via Stargate.

        Args:
            model: Model identifier
            messages: Chat messages
            execution_id: Pipeline execution ID (for tracing)
            step_id: Pipeline step ID (for tracing)
            skip_token_counting: Skip pre-request token counting
                (default: False — token counting runs for slot-aware max_tokens)
            timeout: Request timeout (overrides default)
            map_iteration_request_id: Pre-generated per-iteration request ID
                for cancellation tracking. If None, generates new UUID.
            **params: Additional OpenAI-compatible parameters
                (temperature, max_tokens, response_format, etc.)

        Returns:
            Tuple of (response_dict, map_iteration_request_id,
            snapshot_request_id). snapshot_request_id is the per-call
            unique ID used as X-Internal-Request-ID — matches the
            request/response snapshot filenames.

        Raises:
            ProxyClientError: On request failure
        """
        client = await self._ensure_client()

        # Use provided map_iteration_request_id or generate new
        if map_iteration_request_id is None:
            map_iteration_request_id = str(uuid.uuid4())

        # CRITICAL: Generate unique request_id for THIS call's capacity tracking.
        # map_iteration_request_id is for iteration-level cancellation, but each
        # internal LLM call within the iteration needs its own capacity slot.
        # Handlers like sub_decompose_individual make N parallel calls per iteration
        # (asyncio.gather), and if they share the same request_id, each completion
        # emits MODEL_EXECUTION_COMPLETED with the same ID → N capacity releases
        # for 1 acquisition → double-wake → livelock.
        unique_request_id = str(uuid.uuid4())

        # Build request body
        request_body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,  # Pipeline always non-streaming (aggregates internally)
            **params,
        }

        # Build headers for internal identification
        # Use unique_request_id for X-Internal-Request-ID (becomes proxy request_id)
        request_headers = self._build_request_headers(
            execution_id,
            step_id,
            skip_token_counting,
            timeout,
            unique_request_id,  # Each call gets unique ID for capacity tracking
        )

        # Apply timeout override if specified
        request_timeout = timeout or self._config.request_timeout

        try:
            response = await client.post(
                "/v1/chat/completions",
                json=request_body,
                headers=request_headers,
                timeout=request_timeout,
            )

            if response.status_code >= 400:
                # Parse error detail if available
                try:
                    error_body = response.json()
                    detail = error_body.get("detail", error_body)
                except Exception:
                    detail = response.text

                raise ProxyClientError(
                    f"Stargate returned {response.status_code}",
                    status_code=response.status_code,
                    detail=detail,
                )

            return response.json(), map_iteration_request_id, unique_request_id

        except httpx.TimeoutException as e:
            raise ProxyClientError(
                f"Request timeout after {request_timeout}s",
                status_code=504,
                detail=str(e),
            ) from e

        except httpx.ConnectError as e:
            raise ProxyClientError(
                f"Failed to connect to Stargate: {e}",
                status_code=503,
                detail=str(e),
            ) from e

        except httpx.RemoteProtocolError as e:
            raise ProxyClientError(
                f"HTTP protocol error (connection closed or invalid response): {e}",
                status_code=502,
                detail=str(e),
            ) from e

        except httpx.HTTPError as e:
            error_msg = str(e) if str(e) else f"{e.__class__.__name__}"
            raise ProxyClientError(
                f"HTTP error: {error_msg}",
                detail=str(e),
            ) from e

    async def cancel(
        self, map_iteration_request_id: str, model_id: str | None = None
    ) -> bool:
        """
        Cancel a federation request by map_iteration_request_id.

        Args:
            map_iteration_request_id: The request ID returned from chat_completion()
            model_id: Optional model ID for queue-specific cancellation
                (enables sticky/non-sticky queue cancellation)

        Returns:
            True if cancelled, False if not found or already terminal
        """
        client = await self._ensure_client()

        try:
            body: dict[str, str] = {"request_id": map_iteration_request_id}
            if model_id:
                body["model_id"] = model_id

            response = await client.post(
                "/api/v1/pipeline/cancel",
                json=body,
                timeout=5.0,  # Short timeout for cancel
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("cancelled", False)
            elif response.status_code == 503:
                logger.warning("Proxy not initialized for cancel")
                return False
            else:
                logger.warning(
                    f"Cancel returned {response.status_code}: {response.text}"
                )
                return False

        except Exception as e:
            logger.error(f"Cancel failed for {map_iteration_request_id[:8]}...: {e}")
            return False

    async def embeddings(
        self,
        model: str,
        texts: list[str],
        *,
        execution_id: str | None = None,
        step_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Execute embedding request via Stargate.

        Routes through Stargate's full pipeline:
        - Gateway selection and routing
        - Capacity tracking (via orchestrator)
        - Federation forwarding

        Args:
            model: Embedding model identifier
            texts: Texts to embed
            execution_id: Pipeline execution ID (for tracing)
            step_id: Pipeline step ID (for tracing)
            timeout: Request timeout (overrides default)

        Returns:
            OpenAI-compatible embedding response dict:
            {
                "object": "list",
                "data": [{"object": "embedding", "embedding": [...], "index": 0}, ...],
                "model": "...",
                "usage": {"prompt_tokens": N, "total_tokens": N}
            }

        Raises:
            ProxyClientError: On request failure (includes status_code)
        """
        client = await self._ensure_client()

        # Build request body
        request_body: dict[str, Any] = {
            "model": model,
            "input": texts,
        }

        # Build headers for internal identification
        request_headers = self._build_request_headers(
            execution_id, step_id, skip_token_counting=True, timeout=timeout
        )

        # Apply timeout override if specified
        request_timeout = timeout or self._config.request_timeout

        try:
            response = await client.post(
                "/v1/embeddings",
                json=request_body,
                headers=request_headers,
                timeout=request_timeout,
            )

            if response.status_code >= 400:
                # Parse error detail if available
                try:
                    error_body = response.json()
                    detail = error_body.get("detail", error_body)
                except Exception:
                    detail = response.text

                raise ProxyClientError(
                    f"Stargate returned {response.status_code}",
                    status_code=response.status_code,
                    detail=detail,
                )

            return response.json()

        except httpx.TimeoutException as e:
            raise ProxyClientError(
                f"Request timeout after {request_timeout}s",
                status_code=504,
                detail=str(e),
            ) from e

        except httpx.ConnectError as e:
            raise ProxyClientError(
                f"Failed to connect to Stargate: {e}",
                status_code=503,
                detail=str(e),
            ) from e

        except httpx.RemoteProtocolError as e:
            raise ProxyClientError(
                f"HTTP protocol error (connection closed or invalid response): {e}",
                status_code=502,
                detail=str(e),
            ) from e

        except httpx.HTTPError as e:
            error_msg = str(e) if str(e) else f"{e.__class__.__name__}"
            raise ProxyClientError(
                f"HTTP error: {error_msg}",
                detail=str(e),
            ) from e
