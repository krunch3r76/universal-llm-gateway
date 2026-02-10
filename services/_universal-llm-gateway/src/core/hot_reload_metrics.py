"""
Metrics collection for hot reload functionality.

Provides Prometheus metrics for monitoring configuration reload operations,
file system events, and error tracking.
"""

from universal_logging import get_logger

try:
    from prometheus_client import Counter, Gauge, Histogram, Info
except ImportError:
    # Fallback if prometheus_client is not available
    Counter = Histogram = Gauge = Info = None

logger = get_logger(__name__)


class HotReloadMetrics:
    """Metrics collector for hot reload operations"""

    def __init__(self):
        """Initialize metrics collectors"""
        if Counter is None:
            logger.warning("prometheus_client not available - metrics disabled")
            self.enabled = False
            return

        self.enabled = True

        # Configuration reload metrics
        self.config_reload_total = Counter(
            "gateway_config_reload_total",
            "Total configuration reloads",
            ["status", "model_key", "file_type"],
        )

        self.config_reload_duration = Histogram(
            "gateway_config_reload_duration_seconds",
            "Configuration reload duration in seconds",
            ["status", "model_key"],
        )

        # File system monitoring metrics
        self.config_watch_errors = Counter(
            "gateway_config_watch_errors_total",
            "Configuration file watch errors",
            ["error_type"],
        )

        self.file_events_total = Counter(
            "gateway_file_events_total",
            "Total file system events",
            ["event_type", "file_extension"],
        )

        # System status metrics
        self.active_config_files = Gauge(
            "gateway_active_config_files",
            "Number of active configuration files being monitored",
        )

        self.hot_reload_enabled = Gauge(
            "gateway_hot_reload_enabled",
            "Whether hot reload is currently enabled (1=enabled, 0=disabled)",
        )

        self.observer_running = Gauge(
            "gateway_config_observer_running",
            "Whether file system observer is running (1=running, 0=stopped)",
        )

        # Configuration info
        self.config_info = Info(
            "gateway_hot_reload_config", "Hot reload configuration information"
        )

        logger.info("Hot reload metrics initialized")

    def record_reload_attempt(self, model_key: str | None, file_type: str):
        """Record a configuration reload attempt"""
        if not self.enabled:
            return

        self.config_reload_total.labels(
            status="attempted", model_key=model_key or "unknown", file_type=file_type
        ).inc()

    def record_reload_success(
        self, model_key: str | None, file_type: str, duration_seconds: float
    ):
        """Record a successful configuration reload"""
        if not self.enabled:
            return

        self.config_reload_total.labels(
            status="success", model_key=model_key or "unknown", file_type=file_type
        ).inc()

        self.config_reload_duration.labels(
            status="success", model_key=model_key or "unknown"
        ).observe(duration_seconds)

    def record_reload_failure(
        self, model_key: str | None, file_type: str, duration_seconds: float
    ):
        """Record a failed configuration reload"""
        if not self.enabled:
            return

        self.config_reload_total.labels(
            status="failure", model_key=model_key or "unknown", file_type=file_type
        ).inc()

        self.config_reload_duration.labels(
            status="failure", model_key=model_key or "unknown"
        ).observe(duration_seconds)

    def record_file_event(self, event_type: str, file_extension: str):
        """Record a file system event"""
        if not self.enabled:
            return

        self.file_events_total.labels(
            event_type=event_type, file_extension=file_extension
        ).inc()

    def record_watch_error(self, error_type):
        """Record a file system watch error"""
        if not self.enabled:
            return

        # Handle both string and enum values
        error_str = (
            error_type.value if hasattr(error_type, "value") else str(error_type)
        )
        self.config_watch_errors.labels(error_type=error_str).inc()

    def update_active_files_count(self, count: int):
        """Update the count of active configuration files"""
        if not self.enabled:
            return

        self.active_config_files.set(count)

    def update_hot_reload_status(self, enabled: bool):
        """Update hot reload enabled status"""
        if not self.enabled:
            return

        self.hot_reload_enabled.set(1 if enabled else 0)

    def update_observer_status(self, running: bool):
        """Update file system observer running status"""
        if not self.enabled:
            return

        self.observer_running.set(1 if running else 0)

    def update_config_info(
        self,
        watch_directory: str,
        debounce_ms: int,
        recursive: bool,
        supported_formats: list,
    ):
        """Update configuration information"""
        if not self.enabled:
            return

        self.config_info.info(
            {
                "watch_directory": watch_directory,
                "debounce_ms": str(debounce_ms),
                "recursive": str(recursive),
                "supported_formats": ",".join(supported_formats),
            }
        )


# Global metrics instance
hot_reload_metrics = HotReloadMetrics()
