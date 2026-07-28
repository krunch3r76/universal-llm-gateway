from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from src.scheduling.events import SystemShutdown

if TYPE_CHECKING:
    from ..proxy import StargateProxy

logger = get_logger(__name__)


async def shutdown_proxy(proxy: StargateProxy) -> None:
    """Cleanup async components in reverse startup order."""
    logger.info("Shutting down Stargate Proxy...")

    from .pipeline_orphan_sweep import cancel_running_pipelines_for_shutdown

    try:
        reaped = await cancel_running_pipelines_for_shutdown(
            proxy, reason="process_shutdown"
        )
        if reaped:
            logger.info(
                "Orphan-swept %d running pipeline(s) before shutdown", reaped
            )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Pipeline orphan sweep failed: %s", exc)

    # Stop background cleanup task first
    from src.core.gateway_tracker import gateway_tracker

    try:
        await gateway_tracker.stop_background_cleanup()
    except Exception as exc:
        logger.debug("Error stopping background cleanup: %s", exc)

    if proxy.event_bus:
        try:
            await proxy.event_bus.publish(SystemShutdown())
            logger.info("📢 Published SYSTEM_SHUTDOWN event")
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.debug("Failed to emit SYSTEM_SHUTDOWN event: %s", exc)

    # Stop hot-reload watchers FIRST (before components they watch)
    # This prevents reload attempts during component shutdown
    if hasattr(proxy, "pipeline_hot_reload") and proxy.pipeline_hot_reload:
        try:
            await proxy.pipeline_hot_reload.stop()
            logger.info("Pipeline hot-reload stopped")
        except Exception as exc:
            logger.debug("Error stopping pipeline hot-reload: %s", exc)

    if hasattr(proxy, "profile_watcher") and proxy.profile_watcher:
        try:
            await proxy.profile_watcher.stop()
            logger.info("Profile hot-reload stopped")
        except Exception as exc:
            logger.debug("Error stopping profile hot-reload: %s", exc)

    # Shutdown federation (WebSocket cleanup)
    try:
        from systems.federation.integration.lifecycle import shutdown_federation

        await shutdown_federation()
        logger.info("✅ Federation shutdown complete")
    except Exception as exc:
        logger.error(f"❌ Error shutting down federation: {exc}", exc_info=True)

    if proxy.resource_aware_model_manager and hasattr(
        proxy.resource_aware_model_manager, "shutdown"
    ):
        try:
            await proxy.resource_aware_model_manager.shutdown()
            logger.info("✅ Resource management system shutdown complete")
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error(
                "Error shutting down resource management: %s", exc, exc_info=True
            )

    if proxy.websocket_manager:
        await proxy.websocket_manager.stop_broadcasting()
        logger.info("WebSocketManager stopped")

    if proxy.routing_consumer:
        proxy.routing_consumer.stop()
        logger.info("RoutingConsumer stopped")

    if proxy.monitoring_consumer:
        proxy.monitoring_consumer.stop()
        logger.info("MonitoringConsumer stopped")

    if proxy.metrics_consumer:
        proxy.metrics_consumer.stop()
        logger.info("MetricsConsumer stopped")

    if proxy.model_cache_consumer:
        proxy.model_cache_consumer.stop()
        logger.info("ModelCacheConsumer stopped")

    if proxy.resource_consumer:
        proxy.resource_consumer.stop()
        logger.info("ResourceUpdateConsumer stopped")

    if proxy.routing_metrics_consumer:
        proxy.routing_metrics_consumer.stop()
        logger.info("RoutingMetricsConsumer stopped")

    if proxy.gateway_logger:
        proxy.gateway_logger.stop()
        logger.info("GatewayLogger stopped")

    if proxy.shutdown_handler:
        count = proxy.shutdown_handler.get_shutdown_count()
        logger.info(
            "GatewayShutdownHandler stopped (handled %s shutdown events)", count
        )

    if proxy.gateway_manager:
        await proxy.gateway_manager.shutdown()

    if proxy.http_client:
        await proxy.http_client.aclose()

    # Stop debug broadcaster last (capture shutdown events)
    if hasattr(proxy, "_debug_broadcaster") and proxy._debug_broadcaster:
        try:
            await proxy._debug_broadcaster.stop_debug_server()
            logger.debug("Debug event server stopped")
        except Exception as exc:
            logger.debug("Error stopping debug broadcaster: %s", exc)

    logger.info("✅ Stargate Proxy shut down successfully")
