"""
Typed configuration for resource management with validation and error handling.

This module provides strongly-typed configuration loading and validation
for the atomic VRAM reservation system, replacing the previous dict-based
approach with explicit validation and better error messages.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ResourceManagementConfigError(Exception):
    """Raised when resource management configuration is invalid or missing."""

    pass


@dataclass(frozen=True)
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

        # Validate concurrency limits
        if self.max_concurrent_model_loads < 1:
            raise ResourceManagementConfigError(
                f"max_concurrent_model_loads must be >= 1, got {self.max_concurrent_model_loads}"
            )
        if self.max_concurrent_model_loads > 100:
            raise ResourceManagementConfigError(
                f"max_concurrent_model_loads exceeds recommended maximum of 100, got {self.max_concurrent_model_loads}"
            )

        # Validate timeout ranges
        if not (0.1 <= self.model_loading_slot_acquisition_timeout <= 60.0):
            raise ResourceManagementConfigError(
                f"model_loading_slot_acquisition_timeout must be between 0.1 and 60.0 seconds, got {self.model_loading_slot_acquisition_timeout}"
            )

        if not (60 <= self.reservation_timeout <= 7200):  # 1 minute to 2 hours
            raise ResourceManagementConfigError(
                f"reservation_timeout must be between 60 and 7200 seconds, got {self.reservation_timeout}"
            )

        if not (
            10 <= self.reservation_cleanup_interval <= 600
        ):  # 10 seconds to 10 minutes
            raise ResourceManagementConfigError(
                f"reservation_cleanup_interval must be between 10 and 600 seconds, got {self.reservation_cleanup_interval}"
            )

        # Validate cleanup interval vs reservation timeout
        if self.reservation_cleanup_interval >= self.reservation_timeout:
            raise ResourceManagementConfigError(
                f"reservation_cleanup_interval ({self.reservation_cleanup_interval}) must be less than reservation_timeout ({self.reservation_timeout})"
            )

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
                    f"Required field '{field_name}' missing from resource_management configuration"
                )

            value = config_dict[field_name]
            if not isinstance(value, expected_type):
                type_name = (
                    expected_type.__name__
                    if isinstance(expected_type, type)
                    else str(expected_type)
                )
                raise ResourceManagementConfigError(
                    f"Field '{field_name}' must be of type {type_name}, got {type(value).__name__}: {value}"
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


@dataclass(frozen=True)
class GatewayConfig:
    """
    Typed configuration for a single gateway.

    Resource management is now required for all gateways to ensure
    explicit configuration and prevent silent fallback behavior.
    """

    url: str
    name: str
    # Resource management is now required - must come before fields with defaults
    resource_management: ResourceManagementConfig
    timeout: float = 30.0
    connectivity_timeout: float = 1.0
    health_timeout: float = 2.0
    max_concurrent_cpu_models: int = 50
    max_concurrent_gpu_models: int = 10
    api_key: str = ""

    def __post_init__(self) -> None:
        """Validate gateway configuration."""
        if not self.url:
            raise ResourceManagementConfigError("Gateway URL cannot be empty")
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
            ResourceManagementConfigError: If configuration is invalid or resource_management missing
        """

        # Resource management is now required
        if "resource_management" not in config_dict:
            raise ResourceManagementConfigError(
                f"Gateway '{config_dict.get('name', 'unnamed')}' requires resource_management configuration. "
                "The previous optional behavior has been removed for production safety."
            )

        try:
            resource_management = ResourceManagementConfig.from_dict(
                config_dict["resource_management"]
            )
        except ResourceManagementConfigError as e:
            gateway_name = config_dict.get("name", "unnamed")
            raise ResourceManagementConfigError(
                f"Gateway '{gateway_name}' resource management error: {e}"
            )

        return cls(
            url=config_dict.get("url", ""),
            name=config_dict.get("name", ""),
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
            resource_management=resource_management,
        )


def load_gateway_configs(config_path: Path) -> dict[str, GatewayConfig]:
    """
    Load and validate gateway configurations from YAML file.

    Args:
        config_path: Path to gateways.yaml configuration file

    Returns:
        Dictionary mapping gateway names to validated GatewayConfig instances

    Raises:
        ResourceManagementConfigError: If configuration file is invalid or gateways missing resource management
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

    # Load and validate each gateway
    gateway_configs = {}
    for i, gateway_dict in enumerate(gateways):
        if not isinstance(gateway_dict, dict):
            raise ResourceManagementConfigError(
                f"Gateway {i} configuration must be a dictionary"
            )

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


def get_default_resource_management_config() -> ResourceManagementConfig:
    """
    Get default resource management configuration for testing/development.

    Returns:
        Default ResourceManagementConfig with safe development values
    """
    return ResourceManagementConfig(
        max_concurrent_model_loads=1,
        model_loading_slot_acquisition_timeout=0.25,
        reservation_timeout=300,  # 5 minutes
        reservation_cleanup_interval=30,  # 30 seconds
        enable_reservation_monitoring=True,
    )
