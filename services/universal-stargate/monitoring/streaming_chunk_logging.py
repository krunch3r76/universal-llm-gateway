"""
Streaming chunk event logging mixin for the universal-stargate monitoring layer.

Implements the private _StreamingChunkLogging mixin containing
log_streaming_chunk, log_streaming_chunk_async, and log_streaming_chunk_batch.
These publish MonitoringStreamingChunk events (single or batched) with
lightweight chunk data and optional token metrics. Callers: EventLogger
façade. Invariants: the three methods retain their distinct debug logging
(success vs. none), exception phrasing, and payload construction (per-chunk
vs. batch fields) from the original implementation.
"""

from universal_logging import get_logger

from .event_record_metadata import _new_event_id, _utc_timestamp_z
from .events import MonitoringStreamingChunk

logger = get_logger(__name__)


class _StreamingChunkLogging:
    """Private mixin supplying the three streaming-chunk monitoring methods.

    log_streaming_chunk includes a success debug log after publish.
    log_streaming_chunk_async omits the success line and uses non-blocking
    semantics. log_streaming_chunk_batch constructs combined content and
    uses batch-specific fields (start_chunk_number, chunk_count, content)
    with event_type="streaming_chunk_batch". Host must provide event_bus
    and _ensure_serializable. Failure paths are debug-only and never raise.
    """

    async def log_streaming_chunk(
        self,
        chunk_str: str,
        chunk_number: int,
        request_id: str,
        token_metrics: dict | None = None,
    ):
        """
        Send lightweight monitoring update for a streaming chunk.
        Only includes essential fields: request_id, chunk_number, chunk content, and
            optional token_metrics.
        """
        try:
            # Ensure token_metrics is serializable
            serialized_token_metrics = self._ensure_serializable(token_metrics)

            event_data = {
                "id": _new_event_id(),
                "timestamp": _utc_timestamp_z(),
                "type": "streaming_chunk",
                "request_id": request_id,
                "chunk_number": chunk_number,
                "chunk": chunk_str,
                "token_metrics": serialized_token_metrics,
            }

            # Primary: Publish to EventBus for TransportServer to broadcast
            if self.event_bus:
                try:
                    logger.debug(
                        f"Publishing streaming_chunk to EventBus for"
                        f"request {request_id}"
                    )
                    await self.event_bus.publish_nowait(
                        MonitoringStreamingChunk(
                            event_id=event_data["id"],
                            timestamp=event_data["timestamp"],
                            event_type=event_data["type"],
                            request_id=request_id,
                            chunk_number=chunk_number,
                            chunk=chunk_str,
                            token_metrics=serialized_token_metrics,
                        )
                    )
                    logger.debug("Successfully published streaming_chunk to EventBus")
                except Exception as e:
                    logger.debug(f"Failed to publish to EventBus: {e}")

        except Exception as e:
            # Silent failure - monitoring should never break stargate
            logger.debug(f"Failed to send streaming_chunk event: {e}")
            pass

    async def log_streaming_chunk_async(
        self,
        chunk_str: str,
        chunk_number: int,
        request_id: str,
        token_metrics: dict | None = None,
    ):
        """
        Asynchronously send a streaming chunk for monitoring.
        Non-blocking - returns immediately.
        """
        try:
            # Ensure token_metrics is serializable
            serialized_token_metrics = self._ensure_serializable(token_metrics)

            event_data = {
                "id": _new_event_id(),
                "timestamp": _utc_timestamp_z(),
                "type": "streaming_chunk",
                "request_id": request_id,
                "chunk_number": chunk_number,
                "chunk": chunk_str,
                "token_metrics": serialized_token_metrics,
            }

            # Primary: Publish to EventBus for TransportServer to broadcast
            if self.event_bus:
                try:
                    await self.event_bus.publish_nowait(
                        MonitoringStreamingChunk(
                            event_id=event_data["id"],
                            timestamp=event_data["timestamp"],
                            event_type=event_data["type"],
                            request_id=request_id,
                            chunk_number=chunk_number,
                            chunk=chunk_str,
                            token_metrics=serialized_token_metrics,
                        )
                    )
                except Exception as e:
                    logger.debug(f"Failed to publish to EventBus: {e}")

        except Exception as e:
            # Silent failure - monitoring should never break stargate
            logger.debug(f"Failed to queue streaming chunk: {e}")

    async def log_streaming_chunk_batch(
        self,
        chunks: list[str],
        start_chunk_number: int,
        request_id: str,
        token_metrics: dict | None = None,
    ):
        """
        Send batched monitoring update for multiple streaming chunks.
        More efficient than individual chunk logging.
        """
        try:
            # Combine chunks into a single content string
            combined_content = "".join(chunks)

            # Ensure token_metrics is serializable
            serialized_token_metrics = self._ensure_serializable(token_metrics)

            event_data = {
                "id": _new_event_id(),  # Short ID for display
                "timestamp": _utc_timestamp_z(),
                "type": "streaming_chunk_batch",
                "request_id": request_id,
                "start_chunk_number": start_chunk_number,
                "chunk_count": len(chunks),
                "content": combined_content,
                "token_metrics": serialized_token_metrics,
            }

            # Primary: Publish to EventBus for TransportServer to broadcast
            if self.event_bus:
                try:
                    await self.event_bus.publish_nowait(
                        MonitoringStreamingChunk(
                            event_id=event_data["id"],
                            timestamp=event_data["timestamp"],
                            event_type=event_data["type"],
                            request_id=request_id,
                            start_chunk_number=start_chunk_number,
                            chunk_count=len(chunks),
                            content=combined_content,
                            token_metrics=serialized_token_metrics,
                        )
                    )
                except Exception as e:
                    logger.debug(f"Failed to publish batch to EventBus: {e}")

        except Exception as e:
            # Silent failure - monitoring should never break stargate
            logger.debug(f"Failed to send chunk batch monitoring event: {e}")
