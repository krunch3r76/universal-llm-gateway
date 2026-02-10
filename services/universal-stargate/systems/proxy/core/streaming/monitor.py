"""
Stream monitoring integration for StreamHandler.

Handles all monitoring-related concerns for streaming requests:
- Initial chat completion event for GUI display
- Streaming chunk monitoring
- Stream completion logging
"""

from universal_logging import get_logger

from ...utils.request_context import ForwardContext

logger = get_logger(__name__)


class StreamMonitor:
    """
    Handles monitoring for streaming requests.

    Responsibilities:
    - Send initial chat completion event for streaming requests
    - Monitor individual streaming chunks asynchronously
    - Log stream completion summaries
    """

    def __init__(self, monitor, gateway_url: str):
        """
        Initialize the stream monitor.

        Args:
            monitor: StargateMonitor instance for event publishing
            gateway_url: Gateway URL for monitoring events
        """
        self.monitor = monitor
        self.gateway_url = gateway_url

    async def send_initial_completion_event(self, context: ForwardContext):
        """
        Send initial chat completion event for streaming request.

        This allows the GUI to display that a streaming response has started.

        Args:
            context: Forward context with request metadata
        """
        if not context or not self.monitor or not context.metadata:
            return

        try:
            # Use structured data from context for monitoring
            original_request = context.original_request or {}
            modified_request = context.modified_request or {}

            # Send initial chat completion event for GUI display
            logger.info(
                f"🔍 STREAMING: Sending initial chat_completion event for request {context.request_id}"
            )
            await self.monitor.log_chat_completion(
                original_request=original_request,
                modified_request=modified_request,
                middleware_actions=context.middleware_actions or [],
                processing_time_ms=0,
                gateway_endpoint=self.gateway_url,
                request_id=context.request_id,
                token_metrics=context.token_metrics,
                model_metadata=context.metadata or {},
                response_data={
                    "type": "streaming_response",
                    "stream": True,
                    "status": "started",
                },
            )
            logger.info(
                f"🔍 STREAMING: Successfully sent initial chat_completion event for request {context.request_id}"
            )
        except Exception as e:
            logger.debug(f"Failed to send initial chat completion event: {e}")

    async def monitor_chunk_async(
        self,
        chunk_content: str,
        chunk_number: int,
        request_id: str,
        token_metrics: dict | None = None,
    ):
        """
        Monitor a streaming chunk asynchronously.

        This is called via asyncio.create_task() to send chunk monitoring events
        without blocking the main streaming loop.

        Args:
            chunk_content: Content from the chunk
            chunk_number: Sequential chunk number
            request_id: Request ID for the stream
            token_metrics: Optional token metrics for the chunk
        """
        if not chunk_content:
            return

        try:
            await self.monitor.log_streaming_chunk_async(
                chunk_str=chunk_content,
                chunk_number=chunk_number,
                request_id=request_id,
                token_metrics=token_metrics,
            )
        except Exception as e:
            logger.debug(f"Monitoring failed for chunk {chunk_number}: {e}")

    def log_stream_completion(
        self, context: ForwardContext, chunk_count: int, total_time_ms: float
    ):
        """
        Log stream completion summary.

        Args:
            context: Forward context with request metadata
            chunk_count: Total number of chunks processed
            total_time_ms: Total processing time in milliseconds
        """
        if not context:
            return

        try:
            logger.info(
                f"📊 STREAM COMPLETE: request_id={context.request_id}, "
                f"chunks={chunk_count}, "
                f"total_time={total_time_ms:.1f}ms"
            )
        except Exception as e:
            logger.debug(f"Error logging stream completion summary: {e}")
