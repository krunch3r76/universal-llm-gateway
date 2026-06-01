"""
Streaming request handling for StargateProxy.

Handles streaming request forwarding, newline-delimited JSON streaming,
and content filtering.
"""

import json
import uuid
from typing import Any

import httpx
from fastapi import Request
from universal_logging import format_json_for_log, get_logger

from ...utils.analysis_section_filter import create_content_filter
from ...utils.request_context import ForwardContext, extract_model_name
from ..common import ChunkProcessor, ErrorNormalizer

# Remove import - truncation now automatic
from .generator import create_stream_generator
from .monitor import StreamMonitor
from .response_tracker import TrackedStreamingResponse
from .safe_executor import StreamingSafeExecutor

logger = get_logger(__name__)


class StreamHandler:
    """
    Handles streaming request forwarding to the gateway with format conversion.

    Responsibilities:
    - Streaming request forwarding
    - Format conversion: NDJSON (from gateway) → SSE (to client)
    - Newline-delimited JSON parsing and processing
    - Content filtering for streams
    """

    def __init__(
        self, gateway_url: str, http_client: httpx.AsyncClient, config, monitor
    ):
        """
        Initialize the stream handler.

        Args:
            gateway_url: Base URL of the gateway service
            http_client: Async HTTP client for gateway requests
            config: Stargate configuration (for timeout settings)
            monitor: Monitor instance for logging streaming chunks
        """
        self.gateway_url = gateway_url
        self.http_client = http_client
        self.config = config
        self.monitor = monitor
        self.stream_monitor = StreamMonitor(monitor, gateway_url)
        self.streaming_executor = StreamingSafeExecutor(ErrorNormalizer)

    async def forward_streaming_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        context: ForwardContext | None = None,
        request: Request | None = None,
    ):
        """
        Forward a streaming request to the gateway and convert the response format.

        Receives newline-delimited JSON (NDJSON) from the gateway with signal/payload
        structure and converts it to Server-Sent Events (SSE) format for the client.

        Args:
            method: HTTP method (typically POST)
            path: Request path
            headers: HTTP headers
            content: Request body content
            params: Query parameters
            context: Forward context with request metadata
            request: FastAPI Request object for disconnection detection

        Returns:
            StreamingResponse with SSE format (media_type="text/event-stream")
        """
        request_id_short = (
            context.request_id[:8]
            if context and hasattr(context, "request_id")
            else "unknown"
        )
        logger.info(
            f"🎯 [REQ:{request_id_short}]"
            f"stream_handler.forward_streaming_request() ENTRY"
        )

        if not context:
            request_id = str(uuid.uuid4())
            context = ForwardContext(request_id=request_id)
            logger.debug(
                f"Generated minimal context for streaming request: {request_id}"
            )

        logger.debug(
            f"Forwarding streaming request to {path} for request {context.request_id}"
        )

        if content:
            try:
                request_json = json.loads(content.decode("utf-8"))
                body_log = format_json_for_log(request_json)
                logger.debug(
                    f"Streaming request body to Gateway [{context.request_id}]: "
                    f"{body_log}"
                )
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.debug("Could not parse request body for logging")

        # Determine gateway
        if context and context.gateway_instance:
            gateway_url = context.gateway_instance.config.base_url
            http_client = context.gateway_instance.client.get_http_client()
            gateway_name = context.gateway_instance.config.name

            if context.gateway_instance.config.headers:
                headers = {**headers, **context.gateway_instance.config.headers}
        else:
            gateway_url = self.gateway_url
            http_client = self.http_client
            gateway_name = "default"

        url = f"{gateway_url}{path}"
        logger.debug(
            f"Forwarding streaming {method} {path} to gateway '{gateway_name}'"
        )

        # Extract model and create filter
        model_name = extract_model_name(context, content)
        content_filter = create_content_filter(model_name, context.request_id)

        if content_filter:
            logger.info(f"✅ Analysis filter created for streaming model: {model_name}")

        chunk_processor = ChunkProcessor(content_filter=content_filter)

        # Clean headers
        clean_headers = {
            k.lower(): v
            for k, v in headers.items()
            if k.lower() not in ["host", "content-length"]
        }

        # Get timeout from config
        gateway_config = self.config.get_gateway_config()
        streaming_timeout = gateway_config.get("streaming_timeout", 600.0)

        # Create stream generator function
        async def stream_generator():
            async for chunk in create_stream_generator(
                http_client=http_client,
                method=method,
                url=url,
                headers=clean_headers,
                content=content,
                streaming_timeout=streaming_timeout,
                context=context,
                chunk_processor=chunk_processor,
                stream_monitor=self.stream_monitor,
                gateway_name=gateway_name,
                request=request,
            ):
                yield chunk

        # Wrap with StreamingSafeExecutor for error handling
        request_short_id = context.request_id[:8] if context else "unknown"
        logger.info(
            f"🎁 [REQ:{request_short_id}] Creating TrackedStreamingResponse "
            f"(gateway={gateway_url}, model={model_name})"
        )

        return TrackedStreamingResponse(
            self.streaming_executor.execute_streaming_request(
                stream_generator,
                request_id=context.request_id if context else str(uuid.uuid4()),
                operation="streaming_request",
                gateway_url=gateway_url,
                model_id=model_name,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            request_id=context.request_id if context else str(uuid.uuid4()),
            model=model_name,
        )
