"""
Request lifecycle event logging mixin for the universal-stargate EventLogger.

Contains the private _RequestLifecycleLogging mixin class implementing
log_request_info, log_pre_processing, and log_chat_completion. These methods
emit MonitoringRequestInfo, MonitoringPreProcessing, and MonitoringChatCompletion
events. Callers: composed into EventLogger façade. Invariants: exact payload
shapes, serialization, debug logging, warning/error paths, and silent-failure
behavior preserved from the original monolithic implementation.
"""

from universal_logging import get_logger

from .event_record_metadata import _new_event_id, _utc_timestamp_z
from .events import (
    MonitoringChatCompletion,
    MonitoringPreProcessing,
    MonitoringRequestInfo,
)

logger = get_logger(__name__)


class _RequestLifecycleLogging:
    """Private mixin supplying the three request-lifecycle monitoring methods.

    Provides log_request_info (early discovery), log_pre_processing (post-middleware
    transformations), and log_chat_completion (final result). The host class must
    supply self.event_bus and self._ensure_serializable. All behavior, including
    optional token/model fields, stargate_actions, and error handling, matches
    the pre-modularization EventLogger exactly.
    """

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
                "id": _new_event_id(),
                "timestamp": _utc_timestamp_z(),
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
                await self.event_bus.publish_nowait(
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
                "id": _new_event_id(),
                "timestamp": _utc_timestamp_z(),
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
                await self.event_bus.publish_nowait(
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
                "id": _new_event_id(),
                "timestamp": _utc_timestamp_z(),
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
                await self.event_bus.publish_nowait(
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
