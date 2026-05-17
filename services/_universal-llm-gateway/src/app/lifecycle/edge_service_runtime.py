"""Peripheral async services lifecycle (WebSocket rate limiter, state channel
metrics, WebSocket event forwarder).

Startup and shutdown of these services are isolated here so they can be
started during lifespan and stopped during the unified shutdown sequence
without cluttering the main orchestration module.
"""

from .logging_bootstrap import get_gateway_logger


async def start_edge_services(app) -> None:
    """Start the three peripheral edge services.

    Failures in any one service are logged as warnings and do not abort
    gateway startup (best-effort services).
    """
    gateway_logger = get_gateway_logger()

    # Initialize and start WebSocket rate limiter
    try:
        # Relative import: from lifecycle/ subpackage we need ... to reach src/
        from ...middleware.rate_limiter import websocket_rate_limiter

        await websocket_rate_limiter.start()

        if gateway_logger is not None:
            gateway_logger.info("WebSocket rate limiter started")
    except Exception as e:
        if gateway_logger is not None:
            gateway_logger.warning(f"Failed to start WebSocket services: {e}")

    # Start state channel metrics collector
    try:
        from ...core.metrics.state_channel_metrics import state_channel_metrics

        await state_channel_metrics.start()
        if gateway_logger is not None:
            gateway_logger.info("State channel metrics collector started")
    except Exception as e:
        if gateway_logger is not None:
            gateway_logger.warning(f"Failed to start state channel metrics: {e}")

    # Initialize WebSocket event forwarder for Stargate control plane
    try:
        from ..websocket_lifecycle import initialize_websocket_forwarder

        await initialize_websocket_forwarder(app)
    except Exception as e:
        # Use fallback if logger not initialized yet (preserve original behavior)
        if gateway_logger is not None:
            gateway_logger.warning(f"Failed to start WebSocket event forwarder: {e}")
        else:
            print(f"WARNING: Failed to start WebSocket event forwarder: {e}")


async def stop_edge_services(app) -> None:
    """Shutdown the three peripheral edge services.

    Each service is stopped in its own try/except so that a failure in one
    does not prevent the others from shutting down cleanly.
    """
    gateway_logger = get_gateway_logger()

    # Shutdown WebSocket rate limiter
    try:
        from ...middleware.rate_limiter import websocket_rate_limiter

        await websocket_rate_limiter.shutdown()
        if gateway_logger is not None:
            gateway_logger.info("WebSocket rate limiter shutdown completed")
    except Exception as e:
        if gateway_logger is not None:
            gateway_logger.warning(f"Error shutting down rate limiter: {e}")

    # Shutdown state channel metrics collector
    try:
        from ...core.metrics.state_channel_metrics import state_channel_metrics

        await state_channel_metrics.stop()
        if gateway_logger is not None:
            gateway_logger.info("State channel metrics collector shutdown completed")
    except Exception as e:
        if gateway_logger is not None:
            gateway_logger.warning(f"Error shutting down state channel metrics: {e}")

    # Shutdown WebSocket event forwarder
    try:
        from ..websocket_lifecycle import shutdown_websocket_forwarder

        await shutdown_websocket_forwarder(app)
    except Exception as e:
        if gateway_logger is not None:
            gateway_logger.warning(
                f"Error shutting down WebSocket event forwarder: {e}"
            )
