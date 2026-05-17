"""
Stargate error event logging mixin for the universal-stargate monitoring layer.

Implements the private _StargateErrorLogging mixin with log_stargate_error.
The method constructs MonitoringError events, optionally enriching
stargate_actions with GATEWAY_ERROR details extracted from the gateway
payload. Callers: EventLogger façade via inheritance. Invariants: gateway
enrichment logic (append only when present), serialization calls, debug
messages, and exception paths identical to the original implementation.
"""

from universal_logging import get_logger

from .event_record_metadata import _new_event_id, _utc_timestamp_z
from .events import MonitoringError

logger = get_logger(__name__)


class _StargateErrorLogging:
    """Private mixin supplying the stargate error monitoring method.

    log_stargate_error always records an ERROR action and, when
    gateway_error_details is supplied, appends a GATEWAY_ERROR entry using
    the nested message. Uses _ensure_serializable on request and tokens.
    Host class supplies event_bus. All logging text and control flow match
    the pre-modularization version exactly.
    """

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
                "id": _new_event_id(),
                "timestamp": _utc_timestamp_z(),
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
                await self.event_bus.publish_nowait(
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
