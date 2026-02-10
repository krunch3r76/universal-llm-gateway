"""
SmartLogger Health

Health status, emergency fallback, and health checks.
"""

import logging
from typing import TYPE_CHECKING, Any

from universal_logging import INFO

if TYPE_CHECKING:
    from .core import SmartLogger


def get_health_status_dict(smart_logger: "SmartLogger") -> dict[str, Any]:
    """Get comprehensive health status and metrics.

    Args:
        smart_logger: SmartLogger instance

    Returns:
        Health status dict with status, metrics, and service context
    """
    return {
        "status": "healthy" if smart_logger.metrics["error_count"] == 0 else "degraded",
        "initialization_complete": smart_logger._initialized,
        "configuration_loaded": smart_logger.config is not None,
        "active_handlers": len(logging.getLogger().handlers),
        "performance_metrics": smart_logger.metrics.copy(),
        "last_error": smart_logger._last_error,
        "service_context": {
            "service_name": smart_logger.service_name,
            "workspace_root": (
                str(smart_logger.workspace_root)
                if smart_logger.workspace_root
                else None
            ),
            "environment": smart_logger.environment,
        },
    }


def perform_health_check(smart_logger: "SmartLogger") -> bool:
    """Perform comprehensive health check of logging system.

    Args:
        smart_logger: SmartLogger instance

    Returns:
        True if healthy, False otherwise
    """
    try:
        # Import get_logger here (not at module level) to avoid circular dependency
        from universal_logging import get_logger

        test_logger = get_logger("universal_logging.healthcheck")
        test_logger.info("Health check test message")

        for handler in test_logger.handlers:
            if hasattr(handler, "flush"):
                handler.flush()

        return True

    except Exception as e:
        smart_logger.metrics["error_count"] += 1
        smart_logger._last_error = str(e)
        return False


def setup_emergency_fallback(smart_logger: "SmartLogger", original_error: Exception):
    """
    Set up emergency fallback logging when normal setup fails.

    Uses basicConfig directly with no dependencies on universal_logging runtime.
    Reports errors via bootstrap logger (configurable, stderr by default).

    Args:
        smart_logger: SmartLogger instance with context
        original_error: Original exception from failed setup

    Invariant: ∀ call: ¬depends_on(get_logger) (prevents recursion)
    """
    from universal_logging.bootstrap import bootstrap_logger

    smart_logger.metrics["fallback_activations"] += 1

    try:
        # Set up minimal logging via basicConfig (no dependencies)
        logging.basicConfig(
            level=INFO,
            format="[%(asctime)s] %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            force=True,
        )

        # Report failure via bootstrap logger (not regular logging - avoid recursion)
        bootstrap_logger.error(f"Logging setup failed: {original_error}")
        bootstrap_logger.info(f"Service: {smart_logger.service_name}")
        bootstrap_logger.info(f"Workspace: {smart_logger.workspace_root}")
        bootstrap_logger.info(f"Environment: {smart_logger.environment}")
        bootstrap_logger.info("Using basicConfig emergency fallback")

    except Exception as fallback_error:
        # Last resort: bootstrap logger (stderr)
        bootstrap_logger.error(f"Emergency fallback also failed: {fallback_error}")
        # Don't raise - let execution continue with whatever logging is available
