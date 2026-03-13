"""Stargate configuration loader and accessor surface.

This module owns YAML loading, startup validation dispatch, and normalized
getter helpers that downstream proxy components consume at runtime.
"""

import os
from pathlib import Path
from typing import Any

import yaml
from universal_logging import get_logger

from .types import PipelineEventConfig
from .validators import (
    _validate_eviction_hysteresis,
    _validate_model_routing_config,
    _validate_request_queue_config,
    _validate_routing_capacity,
    _validate_scheduler_config,
)

logger = get_logger(__name__)


class StargateConfig:
    """Load YAML settings, enforce startup invariants, and expose normalized
    config accessors.

    This class is created during proxy bootstrap and is consumed by multiple
    subsystems that require stable defaults and deterministic validation errors.
    """

    def __init__(self, config_path: str = "config/stargate_config.yaml"):
        """Initialize configuration state and fail fast if startup config is invalid."""
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self._validate_config()

    def _load_config(self) -> dict[str, Any]:
        """Load YAML configuration from disk and raise on missing or invalid files."""
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
        """Validate key startup sections and reject values that violate invariants."""
        _validate_scheduler_config(self.config.get("scheduler", {}))
        request_queue_config = self.config.get("request_queue", {})
        _validate_request_queue_config(request_queue_config)
        _validate_routing_capacity(self.config)
        _validate_model_routing_config(self.config.get("model_routing", {}))
        queue_timeout = request_queue_config.get("queue_timeout", 1800.0)
        _validate_eviction_hysteresis(self.config, queue_timeout)
        logger.debug("✅ Configuration validation passed")

    def get_token_management_config(self) -> dict[str, Any]:
        """Return token-management options consumed by request preprocessing paths."""
        return self.config.get("token_management", {})

    def get_logging_config(self) -> dict[str, Any]:
        """Return base logging configuration for proxy-local logging setup."""
        return self.config.get("logging", {})

    def get_gateway_config(self) -> dict[str, Any]:
        """Return gateway section used for upstream model execution connectivity."""
        return self.config.get("gateway", {})

    def get_proxy_config(self) -> dict[str, Any]:
        """Return proxy section for request handling and runtime behavior tuning."""
        return self.config.get("proxy", {})

    def get_scheduler_config(self) -> dict[str, Any]:
        """Return scheduler section for admission and queue polling strategy."""
        return self.config.get("scheduler", {})

    def get_gateway_logging_config(self) -> dict[str, Any]:
        """Return gateway logging config with stable defaults for noisy-path control."""
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
        """Return event consumer toggles and retention defaults for local consumers."""
        return self.config.get(
            "event_consumers",
            {
                "enabled": True,
                "history_size": 1000,
            },
        )

    def get_authorization_config(self) -> dict[str, Any]:
        """Return authorization policy section used by authentication checks."""
        return self.config.get("authorization", {})

    def get_model_management_config(self) -> dict[str, Any]:
        """Return model management section for load/unload orchestration behavior."""
        return self.config.get("model_management", {})

    def get_request_queue_config(self) -> dict[str, Any]:
        """Return request queue policy with defaults that defer true limits to
        capacity.
        """
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
        """Return routing metrics emission settings with transport-safe defaults."""
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
        """Return async monitoring config and normalize transports for current
        runtime mode.
        """
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

        tcp_monitoring_env = os.environ.get(
            "STARGATE_ENABLE_TCP_MONITORING", ""
        ).lower()
        enable_tcp_flag = tcp_monitoring_env in ("1", "true")

        transports_raw = config.get("transports", ["unix"])
        if isinstance(transports_raw, str):
            transports: list[str] = [transports_raw]
        else:
            transports = list(transports_raw)

        if enable_tcp_flag:
            if "tcp" not in transports:
                transports.append("tcp")
            logger.info("✅ TCP monitoring enabled via --enable-tcp-monitoring flag")

        if os.environ.get("STARGATE_UNIX_SOCKET"):
            original_had_tcp = "tcp" in transports
            transports = [t for t in transports if t != "tcp"]

            if not transports:
                transports = ["unix"]

            if original_had_tcp:
                logger.info(
                    "🔒 Running in Unix socket mode (STARGATE_UNIX_SOCKET set), "
                    + "TCP monitoring on port 9997 is disabled"
                )

        return {**config, "transports": transports}

    def get_model_routing_config(self) -> dict[str, Any]:
        """Return sticky routing defaults and explicit per-model override mapping."""
        return self.config.get(
            "model_routing",
            {
                "default_sticky": True,
                "sticky_overrides": {},
            },
        )

    def get_pipelines_config(self) -> dict[str, Any]:
        """Return pipeline discovery search paths used by pipeline registry loading."""
        return self.config.get(
            "pipelines",
            {
                "search_paths": ["pipelines", "pipelines.local", "~/.pipelines"],
            },
        )

    def get_cloud_proxy_config(self) -> dict[str, Any] | None:
        """Return cloud proxy config when present, otherwise None for disabled mode."""
        return self.config.get("cloud_proxy")

    def get_debug_event_config(self) -> dict[str, Any]:
        """Return debug event persistence/socket settings after env-var override
        resolution.
        """
        config = self.config.get("debug_events", {})
        persist_config = config.get("persistence", {})

        env_persist_raw = os.getenv("DEBUG_EVENT_PERSIST")
        if env_persist_raw is not None:
            persist_enabled = env_persist_raw.lower() in ("true", "1")
        else:
            persist_enabled = persist_config.get("enabled", False)

        env_persist_dir = os.getenv("DEBUG_EVENT_PERSIST_DIR")
        persistence = {
            "enabled": persist_enabled,
            "directory": env_persist_dir
            or persist_config.get("directory", "/tmp/stargate-events"),
            "max_file_size_mb": persist_config.get("max_file_size_mb", 50),
            "max_files": persist_config.get("max_files", 3),
            "flush_interval_seconds": persist_config.get("flush_interval_seconds", 1.0),
        }

        env_socket = os.getenv("DEBUG_EVENT_SOCKET")
        socket_path = env_socket or config.get("socket_path")

        return {
            "persistence": persistence,
            "socket_path": socket_path,
        }

    def get_pipeline_event_config(self) -> PipelineEventConfig:
        """Return dedicated pipeline-event persistence settings with env
        override support.
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
