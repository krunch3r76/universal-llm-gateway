"""
Parameter comparison event logging mixin for universal-stargate monitoring.

Implements the private _ParameterComparisonLogging mixin with a single
log_parameter_comparison method that emits MonitoringParameterComparison
events containing user/model/final parameters plus a derived summary.
Callers: EventLogger façade. Invariants: summary calculation, serialization
omission (no _ensure here), debug/ warning/ error paths, and event payload
exactly match the original single-method implementation.
"""

from typing import Any

from universal_logging import get_logger

from .event_record_metadata import _new_event_id, _utc_timestamp_z
from .events import MonitoringParameterComparison

logger = get_logger(__name__)


class _ParameterComparisonLogging:
    """Private mixin supplying the parameter-comparison monitoring method.

    log_parameter_comparison builds an event with full parameter dicts and
    an inline summary (counts of total, user-set, defaults, modified). The
    host class must expose event_bus and _ensure_serializable (unused by
    this path). Behavior, including distinct warning message and error
    logging with exc_info, is unchanged from the pre-split EventLogger.
    """

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
                "id": _new_event_id(),
                "timestamp": _utc_timestamp_z(),
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
                await self.event_bus.publish_nowait(
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
                    "✅ MONITORING: Successfully published parameter_comparison to"
                    "EventBus"
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
