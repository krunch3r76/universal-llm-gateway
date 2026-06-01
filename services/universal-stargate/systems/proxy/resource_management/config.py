"""
Event-driven configuration management for atomic VRAM reservation system.

This module provides strongly-typed configuration with event-driven updates,
replacing file-based polling with async subscription patterns for zero-downtime updates.

Design principles:
- Event-driven configuration updates via async callbacks
- Pure validation functions (no I/O side effects)
- Atomic configuration snapshots for consistency
- Explicit error propagation with structured context
- Single responsibility: one manager per configuration domain
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from weakref import WeakSet

import yaml
from universal_event_bus.actor import Sequential, sequential
from universal_logging import get_logger

logger = get_logger(__name__)


class ResourceManagementConfigError(Exception):
    """Raised when resource management configuration is invalid or missing."""

    def __init__(
        self,
        message: str,
        field_name: str | None = None,
        gateway_name: str | None = None,
    ):
        """
        Create configuration error with structured context.

        Args:
            message: Error description
            field_name: Configuration field that failed validation
            gateway_name: Gateway name if error is gateway-specific
        """
        context_parts = []
        if gateway_name:
            context_parts.append(f"gateway '{gateway_name}'")
        if field_name:
            context_parts.append(f"field '{field_name}'")

        if context_parts:
            full_message = f"[{', '.join(context_parts)}] {message}"
        else:
            full_message = message

        super().__init__(full_message)
        self.field_name = field_name
        self.gateway_name = gateway_name


@dataclass(frozen=True, slots=True)
class ResourceManagementConfig:
    """
    Typed configuration for gateway resource management.

    All fields are required and validated at construction time.
    This replaces the previous dict-based approach with explicit validation.
    """

    # Core concurrency control
    max_concurrent_model_loads: int = field()

    # Timeout configuration (in seconds)
    model_loading_slot_acquisition_timeout: float = field()
    reservation_timeout: int = field()
    reservation_cleanup_interval: int = field()

    # Monitoring and observability
    enable_reservation_monitoring: bool = field(default=True)

    def __post_init__(self) -> None:
        """Validate configuration values at construction time."""
        _validate_resource_management_config(self)

    @classmethod
    def from_dict(cls, config_dict: dict[str, Any]) -> "ResourceManagementConfig":
        """
        Create ResourceManagementConfig from dictionary with validation.

        Args:
            config_dict: Dictionary containing resource management configuration

        Returns:
            Validated ResourceManagementConfig instance

        Raises:
            ResourceManagementConfigError: If required fields missing or invalid
        """

        # Required fields with no defaults
        required_fields = {
            "max_concurrent_model_loads": int,
            "model_loading_slot_acquisition_timeout": (int, float),
            "reservation_timeout": int,
            "reservation_cleanup_interval": int,
        }

        # Check for required fields
        for field_name, expected_type in required_fields.items():
            if field_name not in config_dict:
                raise ResourceManagementConfigError(
                    f"Required field '{field_name}' missing from"
                    f"resource_management configuration"
                )

            value = config_dict[field_name]
            if not isinstance(value, expected_type):
                type_name = (
                    expected_type.__name__
                    if isinstance(expected_type, type)
                    else str(expected_type)
                )
                raise ResourceManagementConfigError(
                    f"Field '{field_name}' must be of type {type_name}, got"
                    f"{type(value).__name__}: {value}"
                )

        # Extract values with proper types
        try:
            return cls(
                max_concurrent_model_loads=int(
                    config_dict["max_concurrent_model_loads"]
                ),
                model_loading_slot_acquisition_timeout=float(
                    config_dict["model_loading_slot_acquisition_timeout"]
                ),
                reservation_timeout=int(config_dict["reservation_timeout"]),
                reservation_cleanup_interval=int(
                    config_dict["reservation_cleanup_interval"]
                ),
                enable_reservation_monitoring=bool(
                    config_dict.get("enable_reservation_monitoring", True)
                ),
            )
        except (ValueError, TypeError) as e:
            raise ResourceManagementConfigError(f"Invalid configuration value: {e}")


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    """
    Typed configuration for a single gateway.

    Resource management is now required for all gateways to ensure
    explicit configuration and prevent silent fallback behavior.

    Transport modes (mutually exclusive):
    - TCP: url="http://hostname:port"
    - Unix socket: socket_path="/path/to/socket" (url defaults to http://localhost)
    """

    url: str
    name: str
    # Resource management is now required - must come before fields with defaults
    resource_management: ResourceManagementConfig
    socket_path: str | None = None
    timeout: float = 30.0
    connectivity_timeout: float = 1.0
    health_timeout: float = 2.0
    max_concurrent_cpu_models: int = 50
    max_concurrent_gpu_models: int = 10
    api_key: str = ""
    capabilities: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate gateway configuration."""
        # Must have either url or socket_path
        if not self.url and not self.socket_path:
            raise ResourceManagementConfigError(
                "Gateway must have either 'url' or 'socket_path' configured"
            )
        if not self.name:
            raise ResourceManagementConfigError("Gateway name cannot be empty")
        if self.timeout <= 0:
            raise ResourceManagementConfigError(
                f"Gateway timeout must be > 0, got {self.timeout}"
            )

    @classmethod
    def from_dict(cls, config_dict: dict[str, Any]) -> "GatewayConfig":
        """
        Create GatewayConfig from dictionary with validation.

        Args:
            config_dict: Dictionary containing gateway configuration

        Returns:
            Validated GatewayConfig instance

        Raises:
            ResourceManagementConfigError: If configuration is invalid or
                resource_management missing
        """
        gateway_name = config_dict.get("name", "unnamed")

        # Validate gateway configuration structure
        _validate_gateway_config_dict(config_dict, gateway_name)

        try:
            resource_management = ResourceManagementConfig.from_dict(
                config_dict["resource_management"]
            )
        except ResourceManagementConfigError as e:
            # Re-raise with gateway context if not already present
            if not e.gateway_name:
                raise ResourceManagementConfigError(str(e), e.field_name, gateway_name)
            raise

        # Handle socket_path: derive url as http://localhost when socket_path is set
        socket_path = config_dict.get("socket_path")
        url = config_dict.get("url", "")
        if socket_path and not url:
            url = "http://localhost"  # Default base_url for Unix socket transport

        return cls(
            url=url,
            name=gateway_name,
            resource_management=resource_management,
            socket_path=socket_path,
            timeout=float(config_dict.get("timeout", 30.0)),
            connectivity_timeout=float(config_dict.get("connectivity_timeout", 1.0)),
            health_timeout=float(config_dict.get("health_timeout", 2.0)),
            max_concurrent_cpu_models=int(
                config_dict.get("max_concurrent_cpu_models", 50)
            ),
            max_concurrent_gpu_models=int(
                config_dict.get("max_concurrent_gpu_models", 10)
            ),
            api_key=config_dict.get("api_key", ""),
            capabilities=dict(config_dict.get("capabilities", {})),
        )


def load_gateway_configs(config_path: Path) -> dict[str, GatewayConfig]:
    """
    Load and validate gateway configurations from YAML file.

    Args:
        config_path: Path to gateways.yaml configuration file

    Returns:
        Dictionary mapping gateway names to validated GatewayConfig instances

    Raises:
        ResourceManagementConfigError: If configuration file is invalid or
            gateways missing resource management
        FileNotFoundError: If configuration file does not exist
        yaml.YAMLError: If YAML parsing fails
    """

    if not config_path.exists():
        raise FileNotFoundError(f"Gateway configuration file not found: {config_path}")

    try:
        with open(config_path) as f:
            config_data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ResourceManagementConfigError(
            f"Failed to parse gateway configuration YAML: {e}"
        )

    if not isinstance(config_data, dict) or "gateways" not in config_data:
        raise ResourceManagementConfigError(
            "Configuration file must contain 'gateways' section"
        )

    gateways = config_data["gateways"]
    if not isinstance(gateways, list):
        raise ResourceManagementConfigError("'gateways' must be a list")

    if not gateways:
        raise ResourceManagementConfigError("At least one gateway must be configured")

    # Load and validate each gateway (skip disabled gateways)
    gateway_configs = {}
    for i, gateway_dict in enumerate(gateways):
        if not isinstance(gateway_dict, dict):
            raise ResourceManagementConfigError(
                f"Gateway {i} configuration must be a dictionary"
            )

        # Skip disabled gateways (enabled defaults to True if not specified)
        if not gateway_dict.get("enabled", True):
            gw_name = gateway_dict.get("name", f"index {i}")
            logger.debug(f"Skipping disabled gateway: {gw_name}")
            continue

        try:
            gateway_config = GatewayConfig.from_dict(gateway_dict)

            # Check for duplicate names
            if gateway_config.name in gateway_configs:
                raise ResourceManagementConfigError(
                    f"Duplicate gateway name: '{gateway_config.name}'"
                )

            gateway_configs[gateway_config.name] = gateway_config

        except ResourceManagementConfigError as e:
            raise ResourceManagementConfigError(f"Gateway {i} validation failed: {e}")

    return gateway_configs


# Pure validation functions (no I/O, no side effects)


def _validate_resource_management_config(config: "ResourceManagementConfig") -> None:
    """
    Pure validation function for ResourceManagementConfig.

    Args:
        config: Configuration to validate

    Raises:
        ResourceManagementConfigError: If validation fails
    """
    # Validate concurrency limits
    if config.max_concurrent_model_loads < 1:
        raise ResourceManagementConfigError(
            f"must be >= 1, got {config.max_concurrent_model_loads}",
            field_name="max_concurrent_model_loads",
        )
    if config.max_concurrent_model_loads > 100:
        raise ResourceManagementConfigError(
            f"exceeds recommended maximum of 100, got"
            f"{config.max_concurrent_model_loads}",
            field_name="max_concurrent_model_loads",
        )

    # Validate timeout ranges
    if not (0.1 <= config.model_loading_slot_acquisition_timeout <= 60.0):
        raise ResourceManagementConfigError(
            f"must be between 0.1 and 60.0 seconds, got"
            f"{config.model_loading_slot_acquisition_timeout}",
            field_name="model_loading_slot_acquisition_timeout",
        )

    if not (60 <= config.reservation_timeout <= 7200):  # 1 minute to 2 hours
        raise ResourceManagementConfigError(
            f"must be between 60 and 7200 seconds, got {config.reservation_timeout}",
            field_name="reservation_timeout",
        )

    if not (
        10 <= config.reservation_cleanup_interval <= 600
    ):  # 10 seconds to 10 minutes
        raise ResourceManagementConfigError(
            f"must be between 10 and 600 seconds, got"
            f"{config.reservation_cleanup_interval}",
            field_name="reservation_cleanup_interval",
        )

    # Validate cleanup interval vs reservation timeout
    if config.reservation_cleanup_interval >= config.reservation_timeout:
        raise ResourceManagementConfigError(
            (
                f"cleanup interval ({config.reservation_cleanup_interval}) "
                f"must be less than reservation timeout "
                f"({config.reservation_timeout})"
            ),
            field_name="reservation_cleanup_interval",
        )

    # Additional safety check: cleanup should be frequent enough
    if config.reservation_cleanup_interval >= config.reservation_timeout / 2:
        raise ResourceManagementConfigError(
            (
                f"cleanup interval ({config.reservation_cleanup_interval}) "
                f"should be < 50% of reservation timeout "
                f"({config.reservation_timeout}) for safety"
            ),
            field_name="reservation_cleanup_interval",
        )


def _validate_gateway_config_dict(
    config_dict: dict[str, Any], gateway_name: str
) -> None:
    """
    Pure validation function for gateway configuration dictionary.

    Args:
        config_dict: Raw configuration dictionary
        gateway_name: Gateway name for error context

    Raises:
        ResourceManagementConfigError: If validation fails
    """
    if "resource_management" not in config_dict:
        raise ResourceManagementConfigError(
            "requires resource_management configuration. The previous optional"
            "behavior has been removed for production safety.",
            gateway_name=gateway_name,
        )


type ConfigUpdateCallback = Callable[[str, GatewayConfig], Awaitable[None]]


class GatewayConfigManager(Sequential):
    """
    Gateway configuration with hot-reload support.

    Flow:
        1. Load initial config from YAML at startup
        2. On reload request: load fresh config, validate, update, notify
        3. Subscribers receive updated config via callbacks

    Uses @sequential decorator instead of locks for reload operations.
    Read operations are lock-free (dict reads are atomic).
    """

    def __init__(self, config_path: Path):
        """
        Initialize configuration manager.

        Inputs:
            config_path: Path to gateways.yaml configuration file
        """
        super().__init__()
        self._config_path = config_path
        self._configs: dict[str, GatewayConfig] = {}
        self._subscribers: WeakSet[ConfigUpdateCallback] = WeakSet()

    async def initialize(self) -> None:
        """
        Load initial configuration and start executor.

        Called once at startup, no concurrency concerns.
        """
        await self._start_executor()
        self._configs = await self._load_configs_from_disk()

    async def shutdown(self) -> None:
        """Stop the sequential executor."""
        await self._stop_executor()

    async def get_gateway_config(self, gateway_name: str) -> GatewayConfig:
        """
        Get current configuration for a specific gateway.

        Inputs:
            gateway_name: Name of gateway

        Outputs:
            Current GatewayConfig snapshot

        No sequentiality needed: Dict reads are atomic.
        """
        if gateway_name not in self._configs:
            available = list(self._configs.keys())
            raise ResourceManagementConfigError(
                f"Gateway '{gateway_name}' not found. Available: {available}"
            )
        return self._configs[gateway_name]

    async def get_all_gateway_configs(self) -> dict[str, GatewayConfig]:
        """
        Get atomic snapshot of all gateway configurations.

        Outputs:
            Immutable copy of current gateway configurations

        No sequentiality needed: Dict.copy() is atomic.
        """
        return self._configs.copy()

    @sequential
    async def reload_gateway_config(self, gateway_name: str) -> GatewayConfig:
        """
        Reload configuration for specific gateway and notify subscribers.

        Inputs:
            gateway_name: Name of gateway to reload

        Outputs:
            Updated GatewayConfig

        Sequential execution: Multi-step operation with awaits.
        - Loads config from disk (I/O)
        - Notifies listeners (potentially network I/O)
        """
        # Load fresh configuration
        fresh_configs = await self._load_configs_from_disk()

        if gateway_name not in fresh_configs:
            available = list(fresh_configs.keys())
            raise ResourceManagementConfigError(
                f"Gateway '{gateway_name}' not found in reloaded"
                f"config. Available: {available}"
            )

        # Update specific gateway atomically
        old_config = self._configs.get(gateway_name)
        new_config = fresh_configs[gateway_name]
        self._configs[gateway_name] = new_config

        # Notify subscribers if configuration changed
        if old_config != new_config:
            await self._notify_config_changed(gateway_name, new_config)

        return new_config

    async def subscribe(self, callback: ConfigUpdateCallback) -> None:
        """
        Subscribe to configuration update notifications.

        Inputs:
            callback: Async function called when gateway config changes
                     Signature: async def callback(
                         gateway_name: str, config: GatewayConfig
                     )
        """
        self._subscribers.add(callback)

    async def unsubscribe(self, callback: ConfigUpdateCallback) -> None:
        """
        Unsubscribe from configuration update notifications.

        Inputs:
            callback: Previously subscribed callback to remove
        """
        self._subscribers.discard(callback)

    async def _load_configs_from_disk(self) -> dict[str, GatewayConfig]:
        """Load and validate gateway configurations from file."""
        import asyncio

        return await asyncio.get_event_loop().run_in_executor(
            None, _load_gateway_configs_sync, self._config_path
        )

    async def _notify_config_changed(
        self, gateway_name: str, config: GatewayConfig
    ) -> None:
        """Notify all subscribers of configuration change."""
        import asyncio

        if not self._subscribers:
            return

        # Create notification tasks for all subscribers
        tasks = []
        callbacks = []
        for callback in list(self._subscribers):
            try:
                task = asyncio.create_task(callback(gateway_name, config))
                tasks.append(task)
                callbacks.append(callback)
            except Exception as e:
                logger.error(
                    f"Failed to create notification task for subscriber"
                    f"{callback.__name__}: {e}",
                    exc_info=True,
                )
                self._subscribers.discard(callback)

        # Wait for all notifications to complete
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for callback, result in zip(callbacks, results, strict=True):
                if isinstance(result, Exception):
                    logger.error(
                        f"Configuration update notification failed for"
                        f"{callback.__name__} f"
                        f"(gateway: {gateway_name}): {result}",
                        exc_info=result,
                    )


def _load_gateway_configs_sync(config_path: Path) -> dict[str, GatewayConfig]:
    """
    Synchronous gateway configuration loading (for executor).

    Pure function that loads and validates configuration from file.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Gateway configuration file not found: {config_path}")

    try:
        with open(config_path) as f:
            config_data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ResourceManagementConfigError(
            f"Failed to parse gateway configuration YAML: {e}"
        )

    if not isinstance(config_data, dict) or "gateways" not in config_data:
        raise ResourceManagementConfigError(
            "Configuration file must contain 'gateways' section"
        )

    gateways = config_data["gateways"]
    if not isinstance(gateways, list):
        raise ResourceManagementConfigError("'gateways' must be a list")

    if not gateways:
        raise ResourceManagementConfigError("At least one gateway must be configured")

    # Load and validate each gateway (skip disabled gateways)
    gateway_configs = {}
    for i, gateway_dict in enumerate(gateways):
        if not isinstance(gateway_dict, dict):
            raise ResourceManagementConfigError(
                f"Gateway {i} configuration must be a dictionary"
            )

        # Skip disabled gateways (enabled defaults to True if not specified)
        if not gateway_dict.get("enabled", True):
            gw_name = gateway_dict.get("name", f"index {i}")
            logger.debug(f"Skipping disabled gateway: {gw_name}")
            continue

        try:
            gateway_config = GatewayConfig.from_dict(gateway_dict)

            # Check for duplicate names
            if gateway_config.name in gateway_configs:
                raise ResourceManagementConfigError(
                    f"Duplicate gateway name: '{gateway_config.name}'"
                )

            gateway_configs[gateway_config.name] = gateway_config

        except ResourceManagementConfigError as e:
            raise ResourceManagementConfigError(f"Gateway {i} validation failed: {e}")

    return gateway_configs


# REMOVED: load_gateway_configs - use GatewayConfigManager instead
# REMOVED: reload_gateway_config - use GatewayConfigManager.reload_gateway_config
# instead
# REMOVED: get_default_resource_management_config - moved to tests.fixtures
