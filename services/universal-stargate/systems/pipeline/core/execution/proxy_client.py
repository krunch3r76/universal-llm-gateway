"""HTTP client for pipeline → Stargate internal communication."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

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
        3600.0  # Safety-net ceiling; real per-iteration timeout enforced by MapExecutor
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


def _error_message(status_code: int, detail: Any) -> str:
    """Build error message that includes upstream error detail when available."""
    base = f"Stargate returned {status_code}"
    if isinstance(detail, dict):
        error_info = detail.get("error", {})
        if isinstance(error_info, dict) and "message" in error_info:
            return f"{base}: {error_info['message']}"
    return base


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
        self._active_requests: int = 0

    async def _write_timeout_diagnostic(
        self,
        *,
        endpoint: str,
        timeout_seconds: float,
        request_body: dict[str, Any],
        request_headers: dict[str, str],
        execution_id: str | None,
        step_id: str | None,
        detail: str,
        queue_wait_seconds: float | None = None,
        inference_elapsed_seconds: float | None = None,
        timeout_type: str | None = None,
    ) -> str | None:
        """Persist timeout diagnostic report for forensic debugging."""
        data_dir = Path(os.getenv("DATA_DIR", "/tmp"))
        report_dir = data_dir / "pipeline-timeout-diagnostics"
        request_id = request_headers.get("X-Internal-Request-ID", "")
        safe_request_id = request_id.replace("/", "-")[:32] or "unknown"
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )
        filename = f"{timestamp}_{endpoint.strip('/').replace('/', '-')}_{safe_request_id}.json"
        report_path = report_dir / filename

        report_payload: dict[str, Any] = {
            "timestamp": timestamp,
            "endpoint": endpoint,
            "timeout_seconds": timeout_seconds,
            "execution_id": execution_id,
            "step_id": step_id,
            "request_id": request_id or None,
            "cancel_group": request_headers.get("X-Pipeline-Cancel-Group"),
            "queue_wait_seconds": queue_wait_seconds,
            "inference_elapsed_seconds": inference_elapsed_seconds,
            "timeout_type": timeout_type,
            "request_headers": request_headers,
            "request_body": request_body,
            "error_detail": detail,
            "transport": {
                "unix_socket_path": self._config.unix_socket_path,
                "host": self._config.host,
                "port": self._config.port,
            },
        }

        try:
            report_dir.mkdir(parents=True, exist_ok=True)
            json_payload = json.dumps(report_payload, indent=2, ensure_ascii=False)
            await asyncio.to_thread(report_path.write_text, json_payload, "utf-8")
            return str(report_path)
        except Exception as dump_error:  # pragma: no cover - diagnostic-only best effort
            logger.warning("Failed to write timeout diagnostic report: %s", dump_error)
            return None

    async def _raise_timeout_error(
        self,
        *,
        endpoint: str,
        request_timeout: float,
        request_body: dict[str, Any],
        request_headers: dict[str, str],
        execution_id: str | None,
        step_id: str | None,
        exception: httpx.TimeoutException,
        request_kind: str,
    ) -> NoReturn:
        """Write diagnostic report and raise standardized timeout error."""
        report_path = await self._write_timeout_diagnostic(
            endpoint=endpoint,
            timeout_seconds=request_timeout,
            request_body=request_body,
            request_headers=request_headers,
            execution_id=execution_id,
            step_id=step_id,
            detail=str(exception),
        )
        if report_path:
            logger.error(
                "Pipeline %s request timed out after %.1fs; diagnostic=%s",
                request_kind,
                request_timeout,
                report_path,
            )
        raise ProxyClientError(
            f"Request timeout after {request_timeout}s",
            status_code=504,
            detail=str(exception),
        ) from exception

    @staticmethod
    def _raise_httpx_transport_error(exception: httpx.HTTPError) -> NoReturn:
        """Normalize httpx transport errors to ProxyClientError."""
        if isinstance(exception, httpx.ConnectError):
            raise ProxyClientError(
                f"Failed to connect to Stargate: {exception}",
                status_code=503,
                detail=str(exception),
            ) from exception
        if isinstance(exception, httpx.RemoteProtocolError):
            raise ProxyClientError(
                (
                    "HTTP protocol error (connection closed or invalid response): "
                    f"{exception}"
                ),
                status_code=502,
                detail=str(exception),
            ) from exception

        error_msg = str(exception) if str(exception) else exception.__class__.__name__
        raise ProxyClientError(
            f"HTTP error: {error_msg}",
            detail=str(exception),
        ) from exception

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
        """Close the HTTP client.

        Defers close if requests are still in flight to avoid StreamClosed
        errors.  Logs at ERROR rather than raising — pipeline cleanup calls
        close() from finally blocks, and raising would mask the original error.
        """
        if self._active_requests > 0:
            logger.error(
                "ProxyClient.close() called with %d active request(s) — "
                "deferring close to avoid StreamClosed errors",
                self._active_requests,
            )
            return
        if self._client:
            await self._client.aclose()
            self._client = None

    def _build_request_headers(
        self,
        execution_id: str | None,
        step_id: str | None,
        skip_token_counting: bool,
        timeout: float | None = None,
        *,
        request_id: str | None = None,
        cancel_group: str | None = None,
    ) -> dict[str, str]:
        """Build internal request headers for pipeline identification.

        Args:
            execution_id: Pipeline execution ID (for tracing)
            step_id: Pipeline step ID (for tracing)
            skip_token_counting: Whether to skip token counting
            timeout: Request timeout (passed to stargate for federation)
            request_id: Per-call unique ID for capacity tracking + snapshots.
                Becomes context.request_id in Stargate via X-Internal-Request-ID.
            cancel_group: Iteration-level ID for group cancellation.
                Stargate's MasterRequestTracker indexes requests by this group.
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
        if request_id:
            headers["X-Internal-Request-ID"] = request_id
        if cancel_group:
            headers["X-Pipeline-Cancel-Group"] = cancel_group

        return headers

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        execution_id: str | None = None,
        step_id: str | None = None,
        skip_token_counting: bool = False,
        disable_profile: bool = True,
        profile: str | None = None,
        timeout: float | None = None,
        map_iteration_request_id: str | None = None,
        request_id: str | None = None,
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
            disable_profile: Suppress model-assigned profile injection (default: True
                for this method — caller should pass the effective value resolved from
                step/pipeline options, which defaults to False). Set True to skip all
                profile logic; set False (default pipeline behavior) to allow the
                model's assigned profile (e.g. "gemma4-instruct") to apply.
            profile: Explicit profile to apply (overrides model assignment).
                Passed as ?filter= query param. Takes effect only when
                disable_profile=False, or forces the named profile when set.
            timeout: Request timeout (overrides default)
            map_iteration_request_id: Pre-generated per-iteration request ID
                for cancellation tracking. If None, generates new UUID.
            request_id: Pre-generated unique request ID for capacity tracking.
                Becomes X-Internal-Request-ID and context.request_id in Stargate.
                If None, generates new UUID. Used by MapExecutor to correlate
                request.processing events before the HTTP call completes.
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

        # CRITICAL: Each call needs its own capacity slot. Handlers like
        # sub_decompose_individual make N parallel calls per iteration; shared
        # request_id → N capacity releases for 1 acquisition → livelock.
        # Pre-generated request_id is only used for the first call of an
        # iteration (for request.processing event correlation); subsequent
        # calls in the same iteration generate fresh UUIDs.
        unique_request_id = request_id or str(uuid.uuid4())

        # Build request body (stream=False enforced after merge — pipeline invariant)
        request_body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            **params,
        }
        request_body["stream"] = False

        request_headers = self._build_request_headers(
            execution_id,
            step_id,
            skip_token_counting,
            timeout,
            request_id=unique_request_id,
            cancel_group=map_iteration_request_id,
        )

        # Build query params for profile control.
        # disable_profile and profile (alias: filter) are Stargate-only query params
        # — they control profile application without entering the forwarded body.
        # When profile is set, do not pass disable_profile — Stargate skips all
        # profile logic when disable_profile=true, so the explicit profile would
        # be ignored.
        query_params: dict[str, str] = {}
        if disable_profile and not profile:
            query_params["disable_profile"] = "true"
        if profile:
            query_params["filter"] = profile

        # Apply timeout override if specified
        request_timeout = timeout or self._config.request_timeout

        self._active_requests += 1
        try:
            response = await client.post(
                "/v1/chat/completions",
                json=request_body,
                headers=request_headers,
                params=query_params,
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
                    _error_message(response.status_code, detail),
                    status_code=response.status_code,
                    detail=detail,
                )

            return response.json(), map_iteration_request_id, unique_request_id

        except httpx.TimeoutException as e:
            await self._raise_timeout_error(
                endpoint="/v1/chat/completions",
                request_timeout=request_timeout,
                request_body=request_body,
                request_headers=request_headers,
                execution_id=execution_id,
                step_id=step_id,
                exception=e,
                request_kind="chat",
            )
        except httpx.HTTPError as e:
            self._raise_httpx_transport_error(e)

        finally:
            self._active_requests -= 1

    async def cancel(
        self, map_iteration_request_id: str, model_id: str | None = None
    ) -> bool:
        """Cancel a cancel group by map_iteration_request_id.

        Sends cancel_group to Stargate, which cancels all requests
        registered under this group (all calls within one map iteration).
        """
        client = await self._ensure_client()

        try:
            body: dict[str, str] = {"cancel_group": map_iteration_request_id}
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

        request_timeout = timeout or self._config.request_timeout

        self._active_requests += 1
        try:
            response = await client.post(
                "/v1/embeddings",
                json=request_body,
                headers=request_headers,
                timeout=request_timeout,
            )

            if response.status_code >= 400:
                try:
                    error_body = response.json()
                    detail = error_body.get("detail", error_body)
                except Exception:
                    detail = response.text

                raise ProxyClientError(
                    _error_message(response.status_code, detail),
                    status_code=response.status_code,
                    detail=detail,
                )

            return response.json()

        except httpx.TimeoutException as e:
            await self._raise_timeout_error(
                endpoint="/v1/embeddings",
                request_timeout=request_timeout,
                request_body=request_body,
                request_headers=request_headers,
                execution_id=execution_id,
                step_id=step_id,
                exception=e,
                request_kind="embedding",
            )
        except httpx.HTTPError as e:
            self._raise_httpx_transport_error(e)

        finally:
            self._active_requests -= 1

    async def rerank(
        self,
        model: str,
        query: str,
        passages: list[str],
        *,
        execution_id: str | None = None,
        step_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Execute rerank request via Stargate.

        Routes through Stargate's full pipeline:
        - Gateway selection and routing
        - Capacity tracking (via orchestrator)
        - Federation forwarding

        Args:
            model: Reranker model identifier
            query: Query to score against passages
            passages: Passages to score
            execution_id: Pipeline execution ID (for tracing)
            step_id: Pipeline step ID (for tracing)
            timeout: Request timeout (overrides default)

        Returns:
            Rerank response dict:
            {
                "scores": [0.91, 0.42, ...],
                "model": "baai-bge-reranker-v2-m3"
            }

        Raises:
            ProxyClientError: On request failure (includes status_code)
        """
        client = await self._ensure_client()

        request_body: dict[str, Any] = {
            "model": model,
            "query": query,
            "passages": passages,
        }

        request_headers = self._build_request_headers(
            execution_id, step_id, skip_token_counting=True, timeout=timeout
        )

        request_timeout = timeout or self._config.request_timeout

        self._active_requests += 1
        try:
            response = await client.post(
                "/api/v1/rerank",
                json=request_body,
                headers=request_headers,
                timeout=request_timeout,
            )

            if response.status_code >= 400:
                try:
                    error_body = response.json()
                    detail = error_body.get("detail", error_body)
                except Exception:
                    detail = response.text

                raise ProxyClientError(
                    _error_message(response.status_code, detail),
                    status_code=response.status_code,
                    detail=detail,
                )

            return response.json()

        except httpx.TimeoutException as e:
            await self._raise_timeout_error(
                endpoint="/api/v1/rerank",
                request_timeout=request_timeout,
                request_body=request_body,
                request_headers=request_headers,
                execution_id=execution_id,
                step_id=step_id,
                exception=e,
                request_kind="rerank",
            )
        except httpx.HTTPError as e:
            self._raise_httpx_transport_error(e)

        finally:
            self._active_requests -= 1
