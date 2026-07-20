"""WebSocket message builders mapping EventBus payloads to wire-format messages.

Converts gateway event dictionaries into typed WebSocketMessage instances for
model lifecycle, resource telemetry, catalog reload, and capacity queue signals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ...events.types import (
    CATALOG_RELOADED,
    COMPUTE_CAPACITY_QUEUE_ACQUIRED,
    COMPUTE_CAPACITY_QUEUE_WAIT,
    GATEWAY_DRAINING,
    GATEWAY_SHUTDOWN,
    INFERENCE_COMPLETED,
    INFERENCE_STARTED,
    MODEL_LOAD_FAILED,
    MODEL_LOADED,
    MODEL_LOADING_PROGRESS,
    MODEL_LOADING_STARTED,
    MODEL_UNLOADED,
    PHANTOM_MODEL_CLEANED,
    PHANTOM_MODEL_DETECTED,
    REQUEST_INFERENCE_STARTED,
    SYSTEM_RESOURCES_UPDATED,
    VRAM_ORPHAN_DETECTED,
    VRAM_STALENESS_DETECTED,
)
from ..messages import (
    WebSocketMessage,
    create_catalog_update_message,
    create_compute_queue_acquired_message,
    create_compute_queue_wait_message,
    create_gateway_draining_message,
    create_gateway_shutdown_message,
    create_model_busy_message,
    create_model_idle_message,
    create_model_load_failed_message,
    create_model_loaded_message,
    create_model_loading_progress_message,
    create_model_loading_started_message,
    create_model_unloaded_message,
    create_phantom_model_cleaned_message,
    create_phantom_model_detected_message,
    create_request_inference_started_message,
    create_resource_update_message,
    create_vram_orphan_detected_message,
    create_vram_staleness_detected_message,
)

if TYPE_CHECKING:
    from ..init_cache import InitDataCache

logger = get_logger(__name__)


def build_sync_message(signal: str, payload: dict[str, Any]) -> WebSocketMessage | None:
    """Build a WebSocket message for synchronous event signals."""
    builders = {
        MODEL_LOADING_STARTED: lambda p: create_model_loading_started_message(
            model_id=p.get("model_id", "unknown")
        ),
        MODEL_LOADING_PROGRESS: lambda p: create_model_loading_progress_message(
            model_id=p.get("model_id", "unknown"),
            phase=p.get("phase", "unknown"),
            pct=p.get("pct", 0),
        ),
        MODEL_LOADED: lambda p: create_model_loaded_message(
            model_id=p.get("model_id", "unknown"),
            vram_mb=p.get("vram_usage_mb", 0),
            ram_mb=p.get("ram_usage_mb", 0),
            context_length=p.get("context_length"),
        ),
        MODEL_LOAD_FAILED: lambda p: create_model_load_failed_message(
            model_id=p.get("model_id", "unknown"),
            error_message=p.get("error_message", "Unknown error"),
            worker_snapshot=p.get("worker_snapshot"),
        ),
        MODEL_UNLOADED: lambda p: create_model_unloaded_message(
            model_id=p.get("model_id", "unknown")
        ),
        INFERENCE_STARTED: lambda p: create_model_busy_message(
            model_id=p.get("model_id", "unknown")
        ),
        INFERENCE_COMPLETED: lambda p: create_model_idle_message(
            model_id=p.get("model_id", "unknown"),
            last_inference_time=p.get("last_inference_time", 0.0),
        ),
        REQUEST_INFERENCE_STARTED: build_request_inference_started_message,
        SYSTEM_RESOURCES_UPDATED: build_system_resources_update_message,
        GATEWAY_SHUTDOWN: lambda p: create_gateway_shutdown_message(
            gateway_id=p.get("gateway_id", "unknown"),
            reason=p.get("reason", "unknown"),
            timestamp=p.get("timestamp", 0),
        ),
        GATEWAY_DRAINING: lambda p: create_gateway_draining_message(
            gateway_id=p.get("gateway_id", "unknown"),
            reason=p.get("reason", "unknown"),
            timeout=p.get("timeout", 30),
            timestamp=p.get("timestamp", 0),
        ),
        COMPUTE_CAPACITY_QUEUE_WAIT: lambda p: create_compute_queue_wait_message(
            request_id=p["request_id"],
            model_id=p["model_id"],
            compute_type=p["compute_type"],
            queue_position=p["queue_position"],
            active_count=p["active_count"],
            limit=p["limit"],
            timestamp_ms=p["timestamp_ms"],
        ),
        COMPUTE_CAPACITY_QUEUE_ACQUIRED: lambda p: (
            create_compute_queue_acquired_message(
                request_id=p["request_id"],
                model_id=p["model_id"],
                compute_type=p["compute_type"],
                wait_duration_ms=p["wait_duration_ms"],
                queue_position_at_enqueue=p["queue_position_at_enqueue"],
                timestamp_ms=p["timestamp_ms"],
            )
        ),
        VRAM_ORPHAN_DETECTED: build_vram_orphan_detected_message,
        VRAM_STALENESS_DETECTED: build_vram_staleness_detected_message,
        PHANTOM_MODEL_DETECTED: build_phantom_model_detected_message,
        PHANTOM_MODEL_CLEANED: build_phantom_model_cleaned_message,
    }
    if builder := builders.get(signal):
        return builder(payload)
    return None


async def build_async_message(
    signal: str,
    payload: dict[str, Any],
    init_cache: InitDataCache | None,
) -> WebSocketMessage | None:
    """Build a WebSocket message for async event signals requiring init cache."""
    if signal == CATALOG_RELOADED:
        return await build_catalog_update_message(payload, init_cache)
    return None


def build_request_inference_started_message(
    payload: dict[str, Any],
) -> WebSocketMessage | None:
    """Build request-scoped runtime-start message with strict payload checks."""
    try:
        return create_request_inference_started_message(
            request_id=payload["request_id"],
            model_id=payload["model_id"],
            gateway_url=payload["gateway_url"],
            correlation_id=payload.get("correlation_id"),
        )
    except KeyError:
        logger.exception(
            "Malformed request.inference.started payload in event_forwarder: "
            "keys=%s payload=%s",
            list(payload.keys()),
            payload,
        )
        return None


def build_system_resources_update_message(
    payload: dict[str, Any],
) -> WebSocketMessage:
    """Build resource update message with forwarding diagnostics."""
    logger.info(
        f"📡 Forwarding SYSTEM_RESOURCES_UPDATED to Stargate: "
        f"available_vram={payload.get('available_vram_mb', 0)}MB, "
        f"available_ram={payload.get('available_ram_mb', 0)}MB"
    )
    return create_resource_update_message(
        available_vram_mb=payload.get("available_vram_mb", 0),
        available_ram_mb=payload.get("available_ram_mb", 0),
        total_vram_mb=payload.get("total_vram_mb"),
        total_ram_mb=payload.get("total_ram_mb"),
        model_vram=payload.get("model_vram"),
    )


async def build_catalog_update_message(
    payload: dict[str, Any],
    init_cache: InitDataCache | None,
) -> WebSocketMessage:
    """Build catalog update message with fresh cache data when available."""
    reason = payload.get("reason", "reload")
    models = None
    catalog = None

    if init_cache:
        init_data = await init_cache.get_init_data()
        models = init_data.get("models", [])
        catalog = init_data.get("catalog", {})
        logger.info(
            f"🔔 Creating CATALOG_UPDATE message: "
            f"{len(models) if models else 0} models, reason={reason}"
        )
    else:
        logger.warning(
            "⚠️ CATALOG_RELOADED but init_cache is None - cannot send model list"
        )

    return create_catalog_update_message(
        reason=reason,
        models=models,
        catalog=catalog,
    )


def build_vram_orphan_detected_message(payload: dict[str, Any]) -> WebSocketMessage:
    """Map VRAM_ORPHAN_DETECTED event payload fields to a Stargate WebSocket message."""
    return create_vram_orphan_detected_message(
        hardware_used_mb=payload.get("hardware_used_mb", 0),
        catalog_used_mb=payload.get("catalog_used_mb", 0),
        discrepancy_mb=payload.get("discrepancy_mb", 0),
        tracked_models=payload.get("tracked_models", []),
    )


def build_vram_staleness_detected_message(payload: dict[str, Any]) -> WebSocketMessage:
    """Map VRAM_STALENESS_DETECTED event payload fields to a Stargate WebSocket message."""
    return create_vram_staleness_detected_message(
        hardware_used_mb=payload.get("hardware_used_mb", 0),
        catalog_used_mb=payload.get("catalog_used_mb", 0),
        discrepancy_mb=payload.get("discrepancy_mb", 0),
        tracked_models=payload.get("tracked_models", []),
    )


def build_phantom_model_detected_message(payload: dict[str, Any]) -> WebSocketMessage:
    """Map PHANTOM_MODEL_DETECTED event payload fields to a Stargate WebSocket message."""
    return create_phantom_model_detected_message(
        model_id=payload.get("model_id", "unknown"),
        process_status=payload.get("process_status", "unknown"),
        tracker_status=payload.get("tracker_status"),
    )


def build_phantom_model_cleaned_message(payload: dict[str, Any]) -> WebSocketMessage:
    """Map PHANTOM_MODEL_CLEANED event payload fields to a Stargate WebSocket message."""
    return create_phantom_model_cleaned_message(
        model_id=payload.get("model_id", "unknown"),
        success=bool(payload.get("success", False)),
        vram_freed_mb=payload.get("vram_freed_mb"),
    )
