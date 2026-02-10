"""
Gateway Error Interceptor Module

Provides automatic error normalization for gateway HTTP calls.

This module wraps httpx client calls to automatically normalize gateway errors
using ErrorNormalizer (Phase 1), ensuring consistent OpenAI-compliant error
responses across all gateway operations.

Usage:
    # Create interceptor
    interceptor = GatewayErrorInterceptor(ErrorNormalizer)

    # Use safe gateway call
    response = await interceptor.safe_gateway_call(
        client=httpx_client,
        method="POST",
        url="http://gateway/v1/models",
        gateway_name="gateway-1",
        operation="model_load",
        json={"model_id": "gpt-4"}
    )

    # Or wrap client for convenient interface
    wrapped_client = interceptor.wrap_client(httpx_client, "gateway-1")
    response = await wrapped_client.post(url, json=data)

Architecture:
    GatewayErrorInterceptor intercepts all gateway HTTP calls and:
    1. Catches httpx.HTTPStatusError (4xx, 5xx responses)
    2. Catches httpx.TransportError (network, timeout errors)
    3. Normalizes errors using ErrorNormalizer (Phase 1)
    4. Adds gateway context to error messages
    5. Raises HTTPException with OpenAI format

    This ensures ALL gateway errors are properly formatted before reaching
    the client, regardless of the error source or HTTP status code.
"""

import asyncio

import httpx
from fastapi import HTTPException
from universal_logging import get_logger

from .error_normalizer import ErrorNormalizer

logger = get_logger(__name__)


class GatewayErrorInterceptor:
    """
    Intercept and normalize errors from gateway HTTP calls.

    Wraps httpx client calls to automatically normalize gateway errors
    using ErrorNormalizer (Phase 1).
    """

    def __init__(self, error_normalizer: type[ErrorNormalizer]):
        """
        Initialize gateway error interceptor.

        Args:
            error_normalizer: ErrorNormalizer class from Phase 1
        """
        self.error_normalizer = error_normalizer

    async def safe_gateway_call(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        gateway_name: str,
        operation: str,
        **kwargs,
    ) -> httpx.Response:
        """
        Make gateway HTTP call with automatic error normalization.

        This method wraps httpx client calls and automatically normalizes
        any errors using ErrorNormalizer, ensuring consistent OpenAI-compliant
        error responses.

        Args:
            client: httpx.AsyncClient instance
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            url: Full URL to call
            gateway_name: Name of gateway (for error context)
            operation: Operation name (e.g., "model_load", "inference", "token_count")
            **kwargs: Additional httpx request arguments (json, headers, params, etc.)

        Returns:
            httpx.Response on success

        Raises:
            HTTPException: With OpenAI format on any error

        Examples:
            # Model loading
            response = await interceptor.safe_gateway_call(
                client=gateway_client,
                method="POST",
                url=f"{gateway_url}/v1/models",
                gateway_name="gateway-1",
                operation="model_load",
                json={"model_id": "gpt-4", "force": True}
            )

            # Token counting
            response = await interceptor.safe_gateway_call(
                client=gateway_client,
                method="POST",
                url=f"{gateway_url}/api/v1/tokens/count",
                gateway_name="gateway-1",
                operation="token_count",
                json={"model_id": "gpt-4", "messages": [...]}
            )
        """
        try:
            # Execute HTTP request
            response = await client.request(method, url, **kwargs)

            # 202 Accepted is valid for model loading operations (async processing started)
            # The caller will poll for completion, so we should return 202 without raising
            if response.status_code == 202 and operation == "model_load":
                logger.info(
                    f"✅ Gateway '{gateway_name}' returned 202 Accepted for model_load operation - "
                    f"loading started, caller will poll for completion. Returning response without raising error."
                )
                return response

            # DIAGNOSTIC: Log if we're about to raise for status
            if response.status_code >= 400:
                logger.warning(
                    f"⚠️ DIAGNOSTIC [model_load]: About to call raise_for_status() on status {response.status_code} "
                    f"for operation '{operation}'"
                )

            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as e:
            # DIAGNOSTIC: Check if this is a 202 that somehow raised HTTPStatusError
            if e.response.status_code == 202 and operation == "model_load":
                logger.warning(
                    "⚠️ DIAGNOSTIC: HTTPStatusError raised for 202 response in model_load operation. "
                    "This should not happen - returning response anyway."
                )
                return e.response

            # Gateway returned error response (4xx, 5xx)
            # Use ErrorNormalizer to extract and normalize the error
            status, error_dict = self.error_normalizer.normalize_to_openai_format(
                error=e.response,
                default_status=e.response.status_code,
                operation=operation,
                gateway_name=gateway_name,
            )

            logger.error(
                f"❌ DIAGNOSTIC: HTTPStatusError for status {status} during {operation} on {gateway_name}"
            )

            # Add gateway context if not already present
            if "error" in error_dict and "message" in error_dict["error"]:
                message = error_dict["error"]["message"]
                # Only add context if not already prefixed with gateway name
                if not message.startswith(f"[{gateway_name}]"):
                    error_dict["error"]["message"] = (
                        f"Gateway '{gateway_name}' error during {operation}: {message}"
                    )

            logger.warning(
                f"Gateway '{gateway_name}' returned HTTP {status} during {operation}: "
                f"{error_dict['error'].get('message', 'Unknown error')}"
            )

            raise HTTPException(status_code=status, detail=error_dict) from e

        except httpx.TransportError as e:
            # Network/transport error (connection failed, timeout, etc.)
            # These are transient errors - map to 503 Service Unavailable
            status, error_dict = self.error_normalizer.normalize_to_openai_format(
                error=e,
                default_status=503,
                operation=operation,
                gateway_name=gateway_name,
            )

            # Enhance message with gateway context
            error_dict["error"]["message"] = (
                f"Gateway '{gateway_name}' connection error during {operation}: {str(e)}"
            )
            error_dict["error"]["type"] = "service_unavailable"
            error_dict["error"]["code"] = "gateway_connection_failed"

            logger.error(
                f"Gateway '{gateway_name}' transport error during {operation}: {e}",
                exc_info=True,
            )

            raise HTTPException(status_code=503, detail=error_dict) from e

        except asyncio.CancelledError:
            # Preserve cancellation (don't catch or normalize)
            logger.debug(
                f"Gateway call to '{gateway_name}' cancelled during {operation}"
            )
            raise

        except Exception as e:
            # Unexpected error (should be rare)
            logger.error(
                f"Unexpected error calling gateway '{gateway_name}' during {operation}: {e}",
                exc_info=True,
            )

            status, error_dict = self.error_normalizer.normalize_to_openai_format(
                error=e,
                default_status=500,
                operation=f"gateway_{operation}",
                gateway_name=gateway_name,
            )

            raise HTTPException(status_code=status, detail=error_dict) from e

    def wrap_client(
        self, client: httpx.AsyncClient, gateway_name: str
    ) -> "GatewayClientWrapper":
        """
        Create wrapped client with automatic error interception.

        Returns a wrapper that provides the same interface as httpx.AsyncClient
        but automatically intercepts and normalizes errors.

        Args:
            client: httpx.AsyncClient instance to wrap
            gateway_name: Name of gateway for error context

        Returns:
            GatewayClientWrapper instance

        Examples:
            # Wrap client once
            wrapped_client = interceptor.wrap_client(gateway.client, "gateway-1")

            # Use like normal httpx.AsyncClient
            response = await wrapped_client.post(
                url="/v1/models",
                json={"model_id": "gpt-4"}
            )
            # Errors are automatically normalized
        """
        return GatewayClientWrapper(client, gateway_name, self)


class GatewayClientWrapper:
    """
    Wrapper for httpx.AsyncClient that intercepts and normalizes errors.

    Provides the same interface as httpx.AsyncClient but automatically
    normalizes all errors using GatewayErrorInterceptor.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        gateway_name: str,
        interceptor: GatewayErrorInterceptor,
    ):
        """
        Initialize gateway client wrapper.

        Args:
            client: httpx.AsyncClient instance to wrap
            gateway_name: Name of gateway for error context
            interceptor: GatewayErrorInterceptor instance
        """
        self._client = client
        self._gateway_name = gateway_name
        self._interceptor = interceptor

    async def get(self, url: str, **kwargs) -> httpx.Response:
        """HTTP GET with automatic error normalization"""
        return await self._interceptor.safe_gateway_call(
            self._client, "GET", url, self._gateway_name, "get", **kwargs
        )

    async def post(self, url: str, **kwargs) -> httpx.Response:
        """HTTP POST with automatic error normalization"""
        return await self._interceptor.safe_gateway_call(
            self._client, "POST", url, self._gateway_name, "post", **kwargs
        )

    async def put(self, url: str, **kwargs) -> httpx.Response:
        """HTTP PUT with automatic error normalization"""
        return await self._interceptor.safe_gateway_call(
            self._client, "PUT", url, self._gateway_name, "put", **kwargs
        )

    async def delete(self, url: str, **kwargs) -> httpx.Response:
        """HTTP DELETE with automatic error normalization"""
        return await self._interceptor.safe_gateway_call(
            self._client, "DELETE", url, self._gateway_name, "delete", **kwargs
        )

    async def patch(self, url: str, **kwargs) -> httpx.Response:
        """HTTP PATCH with automatic error normalization"""
        return await self._interceptor.safe_gateway_call(
            self._client, "PATCH", url, self._gateway_name, "patch", **kwargs
        )

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """HTTP request with automatic error normalization"""
        return await self._interceptor.safe_gateway_call(
            self._client, method, url, self._gateway_name, "request", **kwargs
        )

    @property
    def base_url(self) -> httpx.URL:
        """Access underlying client's base_url"""
        return self._client.base_url

    @property
    def headers(self) -> httpx.Headers:
        """Access underlying client's headers"""
        return self._client.headers

    @property
    def cookies(self) -> httpx.Cookies:
        """Access underlying client's cookies"""
        return self._client.cookies

    @property
    def timeout(self) -> httpx.Timeout:
        """Access underlying client's timeout"""
        return self._client.timeout
