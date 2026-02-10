"""
Event logger module - handles all event-specific logging.

This module is responsible for:
- Formatting event data
- Publishing events to EventBus
- Handling event-specific logic
"""

import uuid
from datetime import datetime
from typing import Any

from universal_logging import get_logger

# Import monitoring event factories
from .events import (
    MonitoringChatCompletion,
    MonitoringError,
    MonitoringParameterComparison,
    MonitoringPreProcessing,
    MonitoringRequestInfo,
    MonitoringStreamingChunk,
)

logger = get_logger(__name__)


class EventLogger:
    """Handles all event-specific logging"""

    def __init__(self, event_bus, ensure_serializable_func):
        """
        Initialize EventLogger.

        Args:
            event_bus: EventBus instance for publishing events
            ensure_serializable_func: Function to ensure objects are serializable
        """
        self.event_bus = event_bus
        self._ensure_serializable = ensure_serializable_func

    async def log_chat_completion(
        self,
        original_request: dict,
        modified_request: dict,
        middleware_actions: list[str],
        processing_time_ms: float,
        gateway_endpoint: str,
        request_id: str,
        token_metrics: dict | None = None,
        model_metadata: dict | None = None,
        response_data: dict | None = None,
    ):
        """Log chat completion event via EventBus"""
        try:
            # Build event data with serialized objects
            event_data = {
                "id": str(uuid.uuid4())[:8],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "type": "chat_completion",
                "request_id": request_id,
                "original_request": self._ensure_serializable(original_request),
                "modified_request": self._ensure_serializable(modified_request),
                "stargate_actions": middleware_actions,
                "processing_time_ms": round(processing_time_ms, 2),
                "gateway_endpoint": gateway_endpoint,
                "response": response_data,
            }

            # Include optional fields if present
            if token_metrics:
                event_data["token_metrics"] = self._ensure_serializable(token_metrics)
            if model_metadata:
                event_data["model_metadata"] = self._ensure_serializable(model_metadata)

            # Publish to EventBus (single consistent path)
            if self.event_bus:
                logger.debug(
                    f"📤 MONITORING: Publishing chat_completion to EventBus for request {request_id}"
                )
                logger.debug(
                    f"📤 MONITORING: Event data keys: {list(event_data.keys())}"
                )
                logger.debug(
                    f"📤 MONITORING: Event data sample: {str(event_data)[:200]}..."
                )
                await self.event_bus.publish_async_nowait(
                    MonitoringChatCompletion(
                        event_id=event_data["id"],
                        timestamp=event_data["timestamp"],
                        request_id=request_id,
                        original_request=event_data["original_request"],
                        modified_request=event_data["modified_request"],
                        stargate_actions=middleware_actions,
                        processing_time_ms=event_data["processing_time_ms"],
                        gateway_endpoint=gateway_endpoint,
                        response=response_data,
                        token_metrics=event_data.get("token_metrics"),
                        model_metadata=event_data.get("model_metadata"),
                    )
                )
                logger.debug(
                    "✅ MONITORING: Successfully published chat_completion to EventBus"
                )
            else:
                logger.warning(
                    "⚠️ MONITORING: EventBus not available, event will not be sent"
                )

        except Exception as e:
            # Silent failure - monitoring should never break stargate
            logger.error(
                f"❌ MONITORING: Failed to publish chat_completion event: {e}",
                exc_info=True,
            )

    async def log_streaming_chunk(
        self,
        chunk_str: str,
        chunk_number: int,
        request_id: str,
        token_metrics: dict | None = None,
    ):
        """
        Send lightweight monitoring update for a streaming chunk.
        Only includes essential fields: request_id, chunk_number, chunk content, and optional token_metrics.
        """
        try:
            # Ensure token_metrics is serializable
            serialized_token_metrics = self._ensure_serializable(token_metrics)

            event_data = {
                "id": str(uuid.uuid4())[:8],
                "timestamp": datetime.utcnow().isoformat() + "Z",
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
                        f"Publishing streaming_chunk to EventBus for request {request_id}"
                    )
                    await self.event_bus.publish_async_nowait(
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
                "id": str(uuid.uuid4())[:8],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "type": "streaming_chunk",
                "request_id": request_id,
                "chunk_number": chunk_number,
                "chunk": chunk_str,
                "token_metrics": serialized_token_metrics,
            }

            # Primary: Publish to EventBus for TransportServer to broadcast
            if self.event_bus:
                try:
                    await self.event_bus.publish_async_nowait(
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
                "id": str(uuid.uuid4())[:8],  # Short ID for display
                "timestamp": datetime.utcnow().isoformat() + "Z",
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
                    await self.event_bus.publish_async_nowait(
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

    async def log_parameter_comparison(
        self,
        model_id: str,
        user_parameters: dict[str, Any],
        model_defaults: dict[str, Any],
        final_parameters: dict[str, Any],
        parameter_changes: list[dict[str, Any]],
        processing_time_ms: float,
        gateway_endpoint: str,
    ):
        """
        Log detailed parameter comparison data for UI display.
        """
        try:
            event_data = {
                "id": str(uuid.uuid4())[:8],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "type": "parameter_comparison",
                "model_id": model_id,
                "user_parameters": user_parameters,
                "model_defaults": model_defaults,
                "final_parameters": final_parameters,
                "parameter_changes": parameter_changes,
                "processing_time_ms": round(processing_time_ms, 2),
                "gateway_endpoint": gateway_endpoint,
                "summary": {
                    "total_parameters": len(final_parameters),
                    "user_set_parameters": len(user_parameters),
                    "default_applied_parameters": len(
                        [p for p in parameter_changes if p["source"] == "model_default"]
                    ),
                    "modified_parameters": len(
                        [p for p in parameter_changes if p["modified"]]
                    ),
                },
            }

            # Publish to EventBus
            if self.event_bus:
                logger.debug(
                    "📤 MONITORING: Publishing parameter_comparison to EventBus"
                )
                await self.event_bus.publish_async_nowait(
                    MonitoringParameterComparison(
                        event_id=event_data["id"],
                        timestamp=event_data["timestamp"],
                        model_id=model_id,
                        user_parameters=user_parameters,
                        model_defaults=model_defaults,
                        final_parameters=final_parameters,
                        parameter_changes=parameter_changes,
                        processing_time_ms=event_data["processing_time_ms"],
                        gateway_endpoint=gateway_endpoint,
                        summary=event_data["summary"],
                    )
                )
                logger.debug(
                    "✅ MONITORING: Successfully published parameter_comparison to EventBus"
                )
            else:
                logger.warning(
                    "⚠️ MONITORING: EventBus not available, event will not be sent"
                )

        except Exception as e:
            logger.error(
                f"❌ MONITORING: Failed to publish parameter_comparison event: {e}",
                exc_info=True,
            )

    async def log_stargate_error(
        self,
        error_message: str,
        original_request: dict,
        processing_time_ms: float,
        token_metrics: dict | None = None,
        gateway_error_details: dict | None = None,
    ):
        """
        Log stargate processing error with optional gateway error details.
        """
        try:
            event_data = {
                "id": str(uuid.uuid4())[:8],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "type": "stargate_error",
                "error_message": error_message,
                "original_request": self._ensure_serializable(original_request),
                "processing_time_ms": round(processing_time_ms, 2),
                "stargate_actions": [f"ERROR: {error_message}"],
                "token_metrics": self._ensure_serializable(token_metrics),
            }

            # Add gateway error details if provided
            if gateway_error_details:
                event_data["gateway_error_details"] = gateway_error_details
                gateway_msg = gateway_error_details.get("gateway_error", {}).get(
                    "message", "Unknown"
                )
                event_data["stargate_actions"].append(f"GATEWAY_ERROR: {gateway_msg}")

            # Publish to EventBus
            if self.event_bus:
                logger.debug("📤 MONITORING: Publishing error to EventBus")
                await self.event_bus.publish_async_nowait(
                    MonitoringError(
                        event_id=event_data["id"],
                        timestamp=event_data["timestamp"],
                        error_message=error_message,
                        original_request=event_data["original_request"],
                        processing_time_ms=event_data["processing_time_ms"],
                        stargate_actions=event_data["stargate_actions"],
                        token_metrics=event_data.get("token_metrics"),
                        gateway_error_details=gateway_error_details,
                    )
                )
                logger.debug("✅ MONITORING: Successfully published error to EventBus")
            else:
                logger.warning(
                    "⚠️ MONITORING: EventBus not available, event will not be sent"
                )

        except Exception as e:
            logger.error(
                f"❌ MONITORING: Failed to publish error event: {e}", exc_info=True
            )

    async def log_request_info(
        self,
        original_request: dict,
        request_id: str,
        selected_model: str,
        profile_name: str | None = None,
    ):
        """
        Log early request_info event for immediate GUI display.

        Sent as soon as request arrives to show original request immediately.
        """
        try:
            event_data = {
                "id": str(uuid.uuid4())[:8],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "type": "request_info",
                "request_id": request_id,
                "original_request": original_request,
                "modified_request": None,  # Not available yet
                "selected_model": selected_model,
                "profile_name": profile_name,
                "response": None,  # No response yet
            }

            # Publish to EventBus
            if self.event_bus:
                logger.debug(
                    f"📤 MONITORING: Publishing request_info to EventBus for request {request_id}"
                )
                await self.event_bus.publish_async_nowait(
                    MonitoringRequestInfo(
                        event_id=event_data["id"],
                        timestamp=event_data["timestamp"],
                        request_id=request_id,
                        original_request=original_request,
                        selected_model=selected_model,
                        profile_name=profile_name,
                    )
                )
                logger.debug(
                    "✅ MONITORING: Successfully published request_info to EventBus"
                )
            else:
                logger.warning(
                    "⚠️ MONITORING: EventBus not available, event will not be sent"
                )

        except Exception as e:
            logger.error(
                f"❌ MONITORING: Failed to publish request_info event: {e}",
                exc_info=True,
            )

    async def log_pre_processing(
        self,
        original_request: dict,
        modified_request: dict,
        middleware_actions: list[str],
        processing_time_ms: float,
        gateway_endpoint: str,
        request_id: str,
        token_metrics: dict | None = None,
        model_metadata: dict | None = None,
    ):
        """
        Log pre-processing event showing transformations before forwarding.
        """
        try:
            # Determine event type based on token metrics presence
            event_type = (
                "pre_processing_with_tokens" if token_metrics else "pre_processing"
            )

            event_data = {
                "id": str(uuid.uuid4())[:8],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "type": "pre_processing",  # Keep type consistent for GUI compatibility
                "event_subtype": event_type,  # Add subtype to differentiate before/after tokenization
                "request_id": request_id,
                "original_request": self._ensure_serializable(original_request),
                "modified_request": self._ensure_serializable(modified_request),
                "stargate_actions": middleware_actions,
                "processing_time_ms": round(processing_time_ms, 2),
                "gateway_endpoint": gateway_endpoint,
                "token_metrics": self._ensure_serializable(token_metrics),
                "model_metadata": self._ensure_serializable(model_metadata),
                "response": None,  # No response yet
            }

            # Publish to EventBus
            if self.event_bus:
                logger.debug(
                    f"📤 MONITORING: Publishing {event_type} to EventBus for request {request_id}"
                )
                await self.event_bus.publish_async_nowait(
                    MonitoringPreProcessing(
                        event_id=event_data["id"],
                        timestamp=event_data["timestamp"],
                        event_subtype=event_type,
                        request_id=request_id,
                        original_request=event_data["original_request"],
                        modified_request=event_data["modified_request"],
                        stargate_actions=middleware_actions,
                        processing_time_ms=event_data["processing_time_ms"],
                        gateway_endpoint=gateway_endpoint,
                        token_metrics=event_data.get("token_metrics"),
                        model_metadata=event_data.get("model_metadata"),
                    )
                )
                logger.debug(
                    f"✅ MONITORING: Successfully published {event_type} to EventBus"
                )
            else:
                logger.warning(
                    "⚠️ MONITORING: EventBus not available, event will not be sent"
                )

        except Exception as e:
            logger.error(
                f"❌ MONITORING: Failed to publish pre_processing event: {e}",
                exc_info=True,
            )
