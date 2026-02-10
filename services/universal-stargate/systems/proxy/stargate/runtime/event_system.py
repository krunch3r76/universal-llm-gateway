from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from src.scheduling.events import SystemStarted

from ...lifecycle.shutdown import (
    initialize_shutdown_handler as create_shutdown_handler,
)
from ...lifecycle.shutdown import (
    register_gateways_with_tracker,
)
from ..tracking import gateway_tracker

if TYPE_CHECKING:
    from ..proxy import StargateProxy

logger = get_logger(__name__)


def initialize_gateway_logger(proxy: StargateProxy) -> None:
    """Create GatewayLogger when enabled."""
    gateway_logging_config = proxy.config.get_gateway_logging_config()
    if not gateway_logging_config.get("enabled", True):
        proxy.gateway_logger = None
        logger.info("ℹ️  GatewayLogger disabled (using scattered logs)")
        return

    from src.scheduling import GatewayLogger

    proxy.gateway_logger = GatewayLogger(
        event_bus=proxy.event_bus,
        rate_limit_window=gateway_logging_config.get("rate_limit_window", 60.0),
        max_logs_per_window=gateway_logging_config.get("max_logs_per_window", 5),
        log_connectivity_changes=gateway_logging_config.get(
            "log_connectivity_changes", True
        ),
        log_health_changes=gateway_logging_config.get("log_health_changes", True),
    )
    logger.info("✅ GatewayLogger initialized (centralized logging active)")


async def initialize_event_consumers(proxy: StargateProxy) -> None:
    """Spin up all optional event consumers."""
    consumer_config = proxy.config.get_event_consumers_config()
    if not consumer_config.get("enabled", True):
        proxy.routing_consumer = None
        proxy.monitoring_consumer = None
        proxy.metrics_consumer = None
        proxy.model_cache_consumer = None
        proxy.resource_consumer = None
        proxy.routing_metrics_consumer = None
        proxy.routing_decision_consumer = None
        proxy.dashboard = None
        proxy.websocket_manager = None
        logger.info("ℹ️  Event consumers disabled")
        return

    from monitoring.gateway_state_dashboard import GatewayStateDashboard
    from monitoring.websocket_server import WebSocketManager
    from src.scheduling import (
        MetricsConsumer,
        ModelCacheConsumer,
        MonitoringConsumer,
        ResourceUpdateConsumer,
        RoutingConsumer,
        RoutingDecisionConsumer,
        RoutingMetricsConsumer,
    )

    proxy.routing_consumer = RoutingConsumer(event_bus=proxy.event_bus)
    proxy.routing_consumer.start()
    logger.info("✅ RoutingConsumer initialized (event-driven routing active)")

    proxy.monitoring_consumer = MonitoringConsumer(
        event_bus=proxy.event_bus,
        history_size=consumer_config.get("history_size", 1000),
    )
    proxy.monitoring_consumer.start()
    logger.info("✅ MonitoringConsumer initialized (real-time monitoring active)")

    proxy.metrics_consumer = MetricsConsumer(event_bus=proxy.event_bus)
    proxy.metrics_consumer.start()
    logger.info("✅ MetricsConsumer initialized (metrics collection active)")

    routing_metrics_config = proxy.config.get_routing_metrics_config()
    if routing_metrics_config.get("enabled", True):
        proxy.routing_metrics_consumer = RoutingMetricsConsumer(
            event_bus=proxy.event_bus,
            udp_host=routing_metrics_config.get("udp_host", "127.0.0.1"),
            udp_port=routing_metrics_config.get("udp_port", 10001),
            enabled=True,
        )
        proxy.routing_metrics_consumer.start()
        udp_host = routing_metrics_config.get("udp_host")
        udp_port = routing_metrics_config.get("udp_port")
        logger.info(
            "✅ RoutingMetricsConsumer initialized (UDP metrics at %s:%s)",
            udp_host,
            udp_port,
        )
    else:
        proxy.routing_metrics_consumer = None
        logger.info("RoutingMetricsConsumer disabled in configuration")

    # Initialize routing decision consumer for decision trace aggregation
    proxy.routing_decision_consumer = RoutingDecisionConsumer(
        event_bus=proxy.event_bus,
        report_interval_sec=consumer_config.get("decision_report_interval_sec", 60.0),
    )
    proxy.routing_decision_consumer.start()
    logger.info("✅ RoutingDecisionConsumer initialized (decision trace aggregation)")

    # ModelCacheConsumer and ResourceUpdateConsumer require gateway_manager
    # Skip in router-only mode (no local gateway to track)
    if proxy.gateway_manager is not None:
        proxy.model_cache_consumer = ModelCacheConsumer(
            event_bus=proxy.event_bus,
            gateway_manager=proxy.gateway_manager,
        )
        proxy.model_cache_consumer.start()
        logger.info("✅ ModelCacheConsumer initialized (real-time model cache updates)")

        proxy.resource_consumer = ResourceUpdateConsumer(
            event_bus=proxy.event_bus,
            gateway_manager=proxy.gateway_manager,
        )
        proxy.resource_consumer.start()
        logger.info(
            "✅ ResourceUpdateConsumer initialized (real-time resource tracking)"
        )
    else:
        proxy.model_cache_consumer = None
        proxy.resource_consumer = None
        logger.info("⏭️ Skipping local gateway consumers (router-only mode)")

    proxy.dashboard = GatewayStateDashboard(
        monitoring_consumer=proxy.monitoring_consumer,
        metrics_consumer=proxy.metrics_consumer,
        alert_threshold_minutes=consumer_config.get("alert_threshold_minutes", 5),
    )
    logger.info("✅ GatewayStateDashboard initialized")

    proxy.websocket_manager = WebSocketManager(
        monitoring_consumer=proxy.monitoring_consumer
    )
    await proxy.websocket_manager.start_broadcasting()
    logger.info("✅ WebSocketManager initialized (real-time updates active)")


def register_gateways(proxy: StargateProxy) -> None:
    """Register single gateway for fast shutdown handling."""
    # Router-only mode: no local gateway to register
    if proxy._gateway_config is None:  # noqa: SLF001
        logger.info("⏭️ Skipping gateway registration (router-only mode)")
        return
    register_gateways_with_tracker([proxy._gateway_config], gateway_tracker)  # noqa: SLF001


def initialize_shutdown_handler(proxy: StargateProxy) -> None:
    """Initialize gateway shutdown handler."""
    proxy.shutdown_handler = create_shutdown_handler(
        gateway_tracker=gateway_tracker,
        event_bus=proxy.event_bus,
        retry_callback=None,
    )


async def emit_system_started_event(proxy: StargateProxy) -> None:
    """Emit SYSTEM_STARTED after all components are online."""
    if not proxy.event_bus:
        return
    try:
        await proxy.event_bus.publish_async(SystemStarted())
        logger.info("📢 Published SYSTEM_STARTED event")
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.debug("Failed to emit SYSTEM_STARTED event: %s", exc)
