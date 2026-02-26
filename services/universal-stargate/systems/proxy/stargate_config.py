import os
from pathlib import Path
from typing import Any

import yaml
from universal_logging import get_logger

logger = get_logger(__name__)


def _validate_scheduler_config(scheduler_config: dict[str, Any]) -> None:
    """
    Validate scheduler configuration section.

    Pure function with no I/O - only validates data structure.
    """
    if "max_queue_size" in scheduler_config:
        max_queue_size = scheduler_config["max_queue_size"]
        if not isinstance(max_queue_size, int) or max_queue_size < 0:
            raise ValueError(
                "scheduler.max_queue_size must be a non-negative integer, "
                f"got: {max_queue_size}"
            )

    if "gateway_check_interval" in scheduler_config:
        check_interval = scheduler_config["gateway_check_interval"]
        if not isinstance(check_interval, int | float) or check_interval <= 0:
            raise ValueError(
                "scheduler.gateway_check_interval must be a positive number, "
                f"got: {check_interval}"
            )

    if "request_timeout" in scheduler_config:
        request_timeout = scheduler_config["request_timeout"]
        if not isinstance(request_timeout, int | float) or request_timeout <= 0:
            raise ValueError(
                "scheduler.request_timeout must be a positive number, "
                f"got: {request_timeout}"
            )


def _validate_request_queue_config(request_queue_config: dict[str, Any]) -> None:
    """
    Validate request_queue configuration section.

    Pure function with no I/O - only validates data structure.
    """
    if "max_size" in request_queue_config:
        max_size = request_queue_config["max_size"]
        if not isinstance(max_size, int) or max_size < 0:
            raise ValueError(
                "request_queue.max_size must be a non-negative integer, "
                f"got: {max_size}"
            )

    if "max_concurrent_processing" in request_queue_config:
        max_concurrent = request_queue_config["max_concurrent_processing"]
        if not isinstance(max_concurrent, int) or max_concurrent < 1:
            raise ValueError(
                "request_queue.max_concurrent_processing must be a positive integer, "
                f"got: {max_concurrent}"
            )

    # Unified queue timeout (top-level) — used by sticky, non-sticky, master capacity
    if "queue_timeout" in request_queue_config:
        timeout = request_queue_config["queue_timeout"]
        if not isinstance(timeout, int | float) or timeout <= 0:
            raise ValueError(
                f"request_queue.queue_timeout must be a positive number, got: {timeout}"
            )

    # Upstream retry timeout — budget for retrying retryable 502 (federated upstream)
    if "upstream_retry_timeout" in request_queue_config:
        urt = request_queue_config["upstream_retry_timeout"]
        if not isinstance(urt, int | float) or urt <= 0:
            raise ValueError(
                "request_queue.upstream_retry_timeout must be a positive number, "
                f"got: {urt}"
            )

    # Validate non_sticky sub-config (no queue_timeout — use top-level)
    if "non_sticky" in request_queue_config:
        non_sticky = request_queue_config["non_sticky"]
        if not isinstance(non_sticky, dict):
            raise ValueError("request_queue.non_sticky must be a mapping")

        if "enabled" in non_sticky and not isinstance(non_sticky["enabled"], bool):
            raise ValueError(
                f"request_queue.non_sticky.enabled must be a boolean, "
                f"got: {non_sticky['enabled']}"
            )

        if "max_concurrent" in non_sticky:
            raise ValueError(
                "request_queue.non_sticky.max_concurrent is removed. "
                "Capacity: Gateway FifoCapacityGate (parallel_slots)."
            )


def _validate_routing_capacity(config: dict[str, Any]) -> None:
    """Reject removed capacity keys (capacity is gateway-side)."""
    capacity = config.get("routing", {}).get("scoring", {}).get("capacity", {})
    if "max_concurrent_per_gateway" in capacity:
        raise ValueError(
            "routing.scoring.capacity.max_concurrent_per_gateway is REMOVED. "
            "Capacity is now managed by Gateway's FifoCapacityGate (parallel_slots)."
        )


def _validate_model_routing_config(model_routing_config: dict[str, Any]) -> None:
    """
    Validate model routing configuration section.

    Invariant: default_sticky ∈ {True, False} ∧ sticky_overrides ∈ dict[str, bool]
    """
    if "default_sticky" in model_routing_config and not isinstance(
        model_routing_config["default_sticky"], bool
    ):
        raise ValueError(
            "model_routing.default_sticky must be a boolean, "
            f"got: {model_routing_config['default_sticky']}"
        )

    if "sticky_overrides" in model_routing_config:
        overrides = model_routing_config["sticky_overrides"]
        if not isinstance(overrides, dict):
            raise ValueError(
                "model_routing.sticky_overrides must be a mapping of model_id->bool"
            )
        for model_id, sticky in overrides.items():
            if not isinstance(model_id, str) or not model_id:
                raise ValueError(
                    "model_routing.sticky_overrides keys must be non-empty strings"
                )
            if not isinstance(sticky, bool):
                raise ValueError(
                    f"model_routing.sticky_overrides['{model_id}'] must be boolean, "
                    f"got: {sticky}"
                )


class StargateConfig:
    """Configuration management for Stargate proxy"""

    def __init__(self, config_path: str = "config/stargate_config.yaml"):
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self._validate_config()

    def _load_config(self) -> dict[str, Any]:
        """
        Load configuration from YAML file.

        Raises:
            FileNotFoundError: If config file not found
            yaml.YAMLError: If config file invalid
        """
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {self.config_path}. "
                "Set STARGATE_CONFIG env var or create config/stargate_config.yaml"
            )

        with open(self.config_path) as f:
            config = yaml.safe_load(f)
            logger.info(f"Loaded configuration from {self.config_path}")
            return config

    def _validate_config(self) -> None:
        """
        Validate configuration values at startup.

        Raises ValueError for invalid configurations rather than failing silently.
        """
        _validate_scheduler_config(self.config.get("scheduler", {}))
        _validate_request_queue_config(self.config.get("request_queue", {}))
        _validate_routing_capacity(self.config)
        _validate_model_routing_config(self.config.get("model_routing", {}))
        logger.debug("✅ Configuration validation passed")

    def get_token_management_config(self) -> dict[str, Any]:
        """Get token management configuration"""
        return self.config.get("token_management", {})

    def get_logging_config(self) -> dict[str, Any]:
        """Get logging configuration"""
        return self.config.get("logging", {})

    def get_gateway_config(self) -> dict[str, Any]:
        """Get gateway configuration"""
        return self.config.get("gateway", {})

    def get_proxy_config(self) -> dict[str, Any]:
        """Get proxy configuration"""
        return self.config.get("proxy", {})

    def get_scheduler_config(self) -> dict[str, Any]:
        """Get scheduler configuration"""
        return self.config.get("scheduler", {})

    def get_gateway_logging_config(self) -> dict[str, Any]:
        """Get gateway logging configuration with defaults"""
        return self.config.get(
            "gateway_logging",
            {
                "enabled": True,
                "rate_limit_window": 60.0,
                "max_logs_per_window": 5,
                "log_connectivity_changes": True,
                "log_health_changes": True,
            },
        )

    def get_event_consumers_config(self) -> dict[str, Any]:
        """Get event consumers configuration with defaults"""
        return self.config.get(
            "event_consumers",
            {
                "enabled": True,
                "history_size": 1000,
            },
        )

    def get_authorization_config(self) -> dict[str, Any]:
        """Get authorization configuration"""
        return self.config.get("authorization", {})

    def get_model_management_config(self) -> dict[str, Any]:
        """Get model management configuration"""
        return self.config.get("model_management", {})

    def get_request_queue_config(self) -> dict[str, Any]:
        """Get request queue configuration with defaults"""
        # Default high so worker capacity (parallel_slots) is the real limit
        return self.config.get(
            "request_queue",
            {
                "max_size": 1000,
                "max_concurrent_processing": 100,
                "request_timeout": 300,
                "queue_timeout": 1800.0,
                "non_sticky": {
                    "enabled": True,
                },
            },
        )

    def get_routing_metrics_config(self) -> dict[str, Any]:
        """Get routing metrics configuration with defaults"""
        return self.config.get(
            "routing_metrics",
            {
                "enabled": True,
                "udp_host": "127.0.0.1",
                "udp_port": 10001,
                "emit_request_routed": True,
                "emit_model_load_initiated": True,
                "emit_model_load_completed": True,
                "emit_token_count_completed": True,
            },
        )

    def get_async_monitoring_config(self) -> dict[str, Any]:
        """
        Get async monitoring configuration with defaults.

        Automatically disables TCP transport when Stargate is running in Unix
        socket mode (when STARGATE_UNIX_SOCKET environment variable is set).

        Can be explicitly enabled via --enable-tcp-monitoring flag or config file.
        """
        import os

        config = self.config.get(
            "async_monitoring",
            {
                "enabled": True,
                "transports": ["unix"],
                "unix_socket_path": "/tmp/stargate_events.sock",
                "host": "0.0.0.0",
                "port": 9997,
            },
        )

        # Check if TCP monitoring is explicitly enabled via command line
        # Accept both "1" and "true" (case-insensitive) for compatibility
        tcp_monitoring_env = os.environ.get(
            "STARGATE_ENABLE_TCP_MONITORING", ""
        ).lower()
        enable_tcp_flag = tcp_monitoring_env in ("1", "true")

        # If --enable-tcp-monitoring flag is set, add TCP to transports
        if enable_tcp_flag:
            transports = config.get("transports", ["unix"])
            if isinstance(transports, str):
                transports = [transports]

            # Add TCP if not already present
            if "tcp" not in transports:
                transports.append("tcp")

            config = {**config, "transports": transports}
            logger.info("✅ TCP monitoring enabled via --enable-tcp-monitoring flag")

        # Disable TCP monitoring when Stargate itself is in Unix socket mode
        if os.environ.get("STARGATE_UNIX_SOCKET"):
            transports = config.get("transports", ["unix"])
            if isinstance(transports, str):
                transports = [transports]

            # Filter out TCP transport
            original_had_tcp = "tcp" in transports
            transports = [t for t in transports if t != "tcp"]

            # Ensure at least Unix socket transport remains
            if not transports:
                transports = ["unix"]

            config = {**config, "transports": transports}

            if original_had_tcp:
                logger.info(
                    "🔒 Running in Unix socket mode (STARGATE_UNIX_SOCKET set), "
                    + "TCP monitoring on port 9997 is disabled"
                )

        return config

    def get_model_routing_config(self) -> dict[str, Any]:
        """Get model routing configuration with defaults."""
        return self.config.get(
            "model_routing",
            {
                "default_sticky": True,
                "sticky_overrides": {},
            },
        )

    def get_pipelines_config(self) -> dict[str, Any]:
        """Get pipelines configuration with defaults."""
        return self.config.get(
            "pipelines",
            {
                "search_paths": ["pipelines", "pipelines.local", "~/.pipelines"],
            },
        )

    def get_cloud_proxy_config(self) -> dict[str, Any] | None:
        """Get cloud_proxy configuration (None if absent)."""
        return self.config.get("cloud_proxy")

    def get_debug_event_config(self) -> dict[str, Any]:
        """
        Get debug event configuration.

        Environment variables override YAML config:
          - DEBUG_EVENT_PERSIST: Enable file persistence
            (env overrides YAML only if set)
          - DEBUG_EVENT_PERSIST_DIR: Persistence directory
            (env overrides YAML only if set)
          - DEBUG_EVENT_SOCKET: Socket path (optional, for live monitoring)

        Returns:
            dict with keys:
                - persistence: dict (enabled, directory, max_file_size_mb, max_files)
                - socket_path: str | None (optional)
        """
        config = self.config.get("debug_events", {})

        # Persistence config from YAML (defaults in YAML, not here)
        persist_config = config.get("persistence", {})

        # Environment override: only if explicitly set (not a default string)
        env_persist_raw = os.getenv("DEBUG_EVENT_PERSIST")
        if env_persist_raw is not None:
            persist_enabled = env_persist_raw.lower() in ("true", "1")
        else:
            persist_enabled = persist_config.get("enabled", False)

        env_persist_dir = os.getenv("DEBUG_EVENT_PERSIST_DIR")

        default_dir = persist_config.get("directory", "/tmp/stargate-events")
        persistence = {
            "enabled": persist_enabled,
            "directory": env_persist_dir or default_dir,
            "max_file_size_mb": persist_config.get("max_file_size_mb", 50),
            "max_files": persist_config.get("max_files", 3),
            "flush_interval_seconds": persist_config.get("flush_interval_seconds", 1.0),
        }

        # Socket (disabled by default - optional for live monitoring)
        env_socket = os.getenv("DEBUG_EVENT_SOCKET")
        socket_path = env_socket or config.get("socket_path")

        return {
            "persistence": persistence,
            "socket_path": socket_path,
        }

    def get_pipeline_event_config(self) -> dict[str, Any]:
        """
        Get dedicated pipeline event persistence configuration.

        Environment variables override YAML config:
          - PIPELINE_EVENT_PERSIST: Enable/disable dedicated pipeline event persistence

        Returns:
            dict with keys:
                - enabled: bool
                - directory: str
                - max_file_size_mb: int
                - max_files: int
                - flush_interval_seconds: float
                - signal_filter: str
        """
        config = self.config.get("pipeline_events", {})
        persist_config = config.get("persistence", {})

        env_enabled = os.getenv("PIPELINE_EVENT_PERSIST")
        enabled = (
            env_enabled.lower() in ("true", "1")
            if env_enabled is not None
            else persist_config.get("enabled", True)
        )

        return {
            "enabled": enabled,
            "directory": persist_config.get("directory", "/tmp/pipeline-events"),
            "max_file_size_mb": persist_config.get("max_file_size_mb", 10),
            "max_files": persist_config.get("max_files", 2),
            "flush_interval_seconds": persist_config.get("flush_interval_seconds", 0.5),
            "signal_filter": "pipeline.",
        }
