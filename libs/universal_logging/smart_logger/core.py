"""
SmartLogger Core

The main SmartLogger class with public API methods.
Delegates to specialized modules for initialization, configuration, and handlers.
"""

import logging
import time
from typing import Any

from .configuration import (
    load_and_prepare_configuration,
    normalize_log_levels,
    sanitize_formatters,
)
from .handlers import setup_handlers, verify_handlers
from .health import (
    get_health_status_dict,
    perform_health_check,
    setup_emergency_fallback,
)


class SmartLogger:
    """Auto-initializing logger with comprehensive error handling and monitoring."""

    def __init__(self):
        """Lightweight constructor - no heavy work.

        Thread Safety: Not needed. Each event loop has its own SmartLogger
        instance via per-loop storage in universal_logging module.
        """
        self._initialized = False
        self.service_name: str | None = None
        self.workspace_root = None
        self.environment: str | None = None
        self.config: dict[str, Any] | None = None

        # Production monitoring and metrics
        self.metrics: dict[str, Any] = {
            "initialization_time": 0,
            "configuration_load_time": 0,
            "handler_setup_time": 0,
            "total_setup_time": 0,
            "failed_handlers": [],
            "fallback_activations": 0,
            "error_count": 0,
            "warning_count": 0,
        }
        self._last_error: str | None = None

    def _ensure_initialized(self):
        """Lazy initialization with comprehensive error handling.

        Thread Safety: Not needed. Each event loop has its own SmartLogger
        instance. No concurrent access to same instance possible.
        """
        if self._initialized:
            return

        start_time = time.time()

        try:
            self._setup_monitoring()
            self._detect_context()
            self._load_configuration()
            self._setup_handlers()

        except Exception as e:
            self.metrics["error_count"] += 1
            self._last_error = str(e)
            setup_emergency_fallback(self, e)
        finally:
            self.metrics["total_setup_time"] = time.time() - start_time
            self._initialized = True

    def _setup_monitoring(self):
        """Setup monitoring and metrics collection."""
        start_time = time.time()
        try:
            # Placeholder for additional monitoring setup
            self.metrics["initialization_time"] = time.time() - start_time
        except Exception as e:
            self.metrics["error_count"] += 1
            logging.error(f"Monitoring setup failed: {e}")

    def _detect_context(self):
        """Detect service context (name, workspace, environment)."""
        from ..context_detection import (
            detect_environment,
            detect_service_name,
            detect_workspace_root,
        )

        self.service_name = detect_service_name()
        self.workspace_root = detect_workspace_root()
        self.environment = detect_environment()

    def _load_configuration(self):
        """Load and prepare logging configuration."""
        config_start = time.time()
        self.config = load_and_prepare_configuration(
            self.service_name, self.workspace_root
        )
        self.config = sanitize_formatters(self.config)
        self.config = normalize_log_levels(self.config)
        self.metrics["configuration_load_time"] = time.time() - config_start

    def _setup_handlers(self):
        """Configure logging handlers."""
        handler_start = time.time()
        setup_handlers(self)
        verify_handlers(self)
        self.metrics["handler_setup_time"] = time.time() - handler_start

    def get_logger(self, name: str | None = None) -> logging.Logger:
        """Get logger with lazy initialization.

        Args:
            name: Logger name (defaults to service name)

        Returns:
            Configured logging.Logger instance
        """
        self._ensure_initialized()
        if name is None:
            name = self.service_name or "universal"
        return logging.getLogger(name)

    def get_context(self) -> dict[str, Any]:
        """Get detected context information.

        Returns:
            Dict with service_name, workspace_root, and environment
        """
        self._ensure_initialized()
        workspace_str = str(self.workspace_root) if self.workspace_root else None
        return {
            "service_name": self.service_name,
            "workspace_root": workspace_str,
            "environment": self.environment,
        }

    def get_health_status(self) -> dict[str, Any]:
        """Get comprehensive health status and metrics.

        Returns:
            Health status dict with status, metrics, and service context
        """
        return get_health_status_dict(self)

    def _health_check(self) -> bool:
        """Perform comprehensive health check of logging system.

        Returns:
            True if healthy, False otherwise
        """
        return perform_health_check(self)
