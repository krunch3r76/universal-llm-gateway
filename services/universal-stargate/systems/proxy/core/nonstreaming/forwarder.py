"""
Non-streaming request forwarding for StargateProxy.

Handles forwarding non-streaming requests to the gateway and applying response filters.
"""

import asyncio
import json
from typing import Any

import httpx
from fastapi import HTTPException, Response
from universal_logging import get_logger

from ...utils.analysis_section_filter import create_content_filter
from ...utils.request_context import ForwardContext, extract_model_name

logger = get_logger(__name__)


class RequestForwarder:
    """
    Handles non-streaming request forwarding to the gateway.

    Responsibilities:
    - Non-streaming request forwarding
    - Response filtering
    - Gateway communication
    """

    def __init__(self, gateway_url: str, http_client: httpx.AsyncClient, config):
        """
        Initialize the request forwarder.

        Args:
            gateway_url: Base URL of the gateway service
            http_client: Async HTTP client for gateway requests
            config: Stargate configuration (for timeout settings)
        """
        self.gateway_url = gateway_url
        self.http_client = http_client
        self.config = config

    def apply_response_filter(
        self, response_content: bytes, content_filter, model_name: str | None
    ) -> bytes:
        """
        Apply content filter to response content.

        Args:
            response_content: Raw response content bytes
            content_filter: Content filter instance to apply
            model_name: Model name for logging

        Returns:
            Filtered response content bytes
        """
        try:
            # Parse the response
            response_json = json.loads(response_content.decode("utf-8"))

            # Extract content from response
            if isinstance(response_json, dict) and "choices" in response_json:
                choices = response_json.get("choices", [])
                if choices and "message" in choices[0]:
                    content_text = choices[0]["message"].get("content", "")

                    if content_text:
                        # Use the simplified filter method for non-streaming
                        filtered_content = content_filter.filter_content(content_text)

                        # Update the response if content was changed
                        if filtered_content != content_text:
                            response_json["choices"][0]["message"]["content"] = (
                                filtered_content
                            )
                            logger.info(
                                f"Applied analysis filter to non-streaming response"
                                f"for model: {model_name}"
                            )

                            # Reconstruct response
                            return json.dumps(response_json).encode("utf-8")

        except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
            logger.debug(
                f"Failed to apply content filter to non-streaming response: {e}"
            )
        except Exception as e:
            logger.warning(f"Unexpected error applying content filter: {e}")

        return response_content

    async def forward_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        context: ForwardContext | None = None,
        request=None,
    ) -> Response:
        """
        Forward a non-streaming request to the gateway.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: Request path
            headers: HTTP headers
            content: Request body content
            params: Query parameters
            context: Forward context with request metadata
            request: FastAPI Request for disconnection detection

        Returns:
            Response from the gateway

        Raises:
            HTTPException: If request forwarding fails
        """
        # Use gateway from context
        if context and context.gateway_instance:
            gateway_url = context.gateway_instance.config.base_url
            http_client = context.gateway_instance.client.get_http_client()
            gateway_name = context.gateway_instance.config.name

            # Merge gateway-specific headers (authentication)
            if context.gateway_instance.config.headers:
                headers = {**headers, **context.gateway_instance.config.headers}
        else:
            gateway_url = self.gateway_url
            http_client = self.http_client
            gateway_name = "default"

        url = f"{gateway_url}{path}"

        logger.debug(f"Forwarding {method} {path} to gateway '{gateway_name}'")

        # Clean headers
        clean_headers = {
            k.lower(): v
            for k, v in headers.items()
            if k.lower() not in ["host", "content-length"]
        }

        # Get timeout from config (default to 600 seconds for long-running inference)
        gateway_config = self.config.get_gateway_config()
        request_timeout = gateway_config.get("request_timeout", 1200.0)

        # Extract model name and create filter
        model_name = extract_model_name(context, content)
        content_filter = create_content_filter(
            model_name, context.request_id if context else None
        )

        if content_filter:
            logger.info(
                f"✅ Analysis filter created for non-streaming model: {model_name}"
            )

        try:
            # Log request details for correlation tracking
            request_id = context.request_id if context else None
            logger.debug(
                f"🔍 Forwarding request {request_id} to gateway '{gateway_name}':"
                f"{method} {url} (model: {model_name})"
            )

            # Quick pre-flight check - don't start if client already gone
            if request:
                try:
                    if await request.is_disconnected():
                        logger.info(
                            f"🔌 Client disconnected before gateway request "
                            f"{request_id}"
                        )
                        raise HTTPException(
                            status_code=499, detail="Client disconnected"
                        )
                except HTTPException:
                    raise
                except Exception as e:
                    logger.debug(f"Could not check client disconnection: {e}")

            response = await _forward_with_disconnect_race(
                http_client=http_client,
                method=method,
                url=url,
                headers=clean_headers,
                content=content,
                params=params,
                timeout=request_timeout,
                request=request,
                request_id=request_id,
                model_name=model_name,
                gateway_url=gateway_url,
                context=context,
            )

            # Validate response matches request (check model in response if available)
            response_content = response.content
            try:
                response_json = json.loads(response_content.decode("utf-8"))
                response_model = response_json.get("model", "")
                response_id = response_json.get("id", "")

                # Log response details for correlation tracking
                if response_id:
                    logger.debug(
                        f"📥 Response ID from gateway: {response_id} for"
                        f"request {request_id}"
                    )

                # Check for model mismatch
                if response_model and model_name and response_model != model_name:
                    logger.error(
                        f"⚠️ RESPONSE MODEL MISMATCH: Request was for "
                        f"'{model_name}' but response contains model "
                        f"'{response_model}'. Request ID: {request_id}, "
                        f"Response ID: {response_id}. Responses may be "
                        f"mixed up between concurrent requests!"
                    )

                # Log response content preview for debugging (first 100 chars)
                if "choices" in response_json and response_json["choices"]:
                    first_choice = response_json["choices"][0]
                    content_preview = ""
                    if (
                        "message" in first_choice
                        and "content" in first_choice["message"]
                    ):
                        content_preview = first_choice["message"]["content"][:100]
                    elif "text" in first_choice:
                        content_preview = first_choice["text"][:100]

                    if content_preview:
                        logger.debug(
                            f"📄 Response content preview for request"
                            f"{request_id}: {content_preview}..."
                        )

            except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
                # Response might not be JSON or might not have expected fields - that's
                # okay
                logger.debug(f"Could not parse response JSON for validation: {e}")

            logger.debug(
                f"✅ Received response for request {request_id} from gateway"
                f"'{gateway_name}' (status: {response.status_code})"
            )

            # Write response snapshot if debugging enabled (from Gateway)
            from ...debug.request_snapshots import write_response_snapshot

            if response_content and request_id:
                await write_response_snapshot(
                    response_content, request_id, stage="response-from-gateway"
                )

            # Apply content filter if needed
            response_headers = dict(response.headers)

            if content_filter:
                original_length = len(response_content)
                response_content = self.apply_response_filter(
                    response_content, content_filter, model_name
                )

                # Update Content-Length header if content was modified
                if len(response_content) != original_length:
                    response_headers["content-length"] = str(len(response_content))
                    logger.debug(
                        f"Updated Content-Length from {original_length} to"
                        f"{len(response_content)}"
                    )

            # Write final response snapshot if debugging enabled (to client)
            if request_id:
                await write_response_snapshot(
                    response_content, request_id, stage="response-to-client"
                )

            return Response(
                content=response_content,
                status_code=response.status_code,
                headers=response_headers,
            )

        except httpx.RequestError as e:
            error_message = str(e) if str(e) else f"{type(e).__name__}: {repr(e)}"
            logger.error(f"Request error: {error_message}")
            raise HTTPException(
                status_code=502,
                detail={"error": {"message": error_message, "type": "gateway_error"}},
            )


async def _poll_disconnect(request) -> None:
    """Resolve when the upstream client closes its connection."""
    while not await request.is_disconnected():
        await asyncio.sleep(0.5)


async def _forward_with_disconnect_race(
    http_client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict,
    content: bytes | None,
    params: dict | None,
    timeout: float,
    request,
    request_id: str | None,
    model_name: str | None,
    gateway_url: str,
    context: "ForwardContext | None",
) -> httpx.Response:
    """Forward HTTP request, cancelling the outgoing connection on client disconnect.

    ∀ client_disconnect during forwarding:
      outgoing HTTP task cancelled → next-hop TCP connection closed →
      next-hop detects disconnect → cancel propagates down chain without
      out-of-band API calls.

    ∀ httpx.TimeoutException (no upstream client):
      best-effort cancel-request sent to next hop (Gateway endpoint only;
      no-ops silently when next hop is another Stargate).
    """
    http_task = asyncio.create_task(
        http_client.request(
            method=method,
            url=url,
            headers=headers,
            content=content,
            params=params,
            timeout=timeout,
        )
    )

    if request is None:
        return await http_task

    disconnect_task = asyncio.create_task(_poll_disconnect(request))

    try:
        done, pending = await asyncio.wait(
            {http_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        if disconnect_task in done:
            # Cancelling http_task already closed the outgoing TCP connection;
            # the next hop will detect this via its own is_disconnected() poll.
            logger.info(
                f"🔌 Client disconnected - cancelled upstream for "
                f"{(request_id or '')[:8]} (model: {model_name})"
            )
            raise HTTPException(status_code=499, detail="Client disconnected")

        exc = http_task.exception()
        if exc is not None:
            if isinstance(exc, httpx.TimeoutException) and model_name and context:
                cancel_url = (
                    f"{gateway_url}/v1/management/models/{model_name}/cancel-request"
                )
                try:
                    await http_client.post(
                        cancel_url,
                        json={
                            "stream_id": context.request_id,
                            "reason": "client_timeout",
                        },
                        timeout=5.0,
                    )
                except Exception as cancel_err:
                    logger.warning(
                        f"⚠️ Cancel after timeout failed for "
                        f"{(request_id or '')[:8]}: {cancel_err}"
                    )
            raise exc

        return http_task.result()

    except asyncio.CancelledError:
        if not http_task.done():
            http_task.cancel()
            try:
                await http_task
            except (asyncio.CancelledError, Exception):
                pass
        raise
