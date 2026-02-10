"""
Configuration loader for batch routing.

Loads scheduling weights and other batch routing config from stargate_config.yaml.
Falls back to sensible defaults if config section missing.

Domain: Proxy
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

# Default weights (used if config missing or invalid)
DEFAULT_WEIGHTS = {
    "critical_path": 50.0,
    "request_count": 10.0,
    "parallel_enablement": 20.0,
    "depth_penalty": 5.0,
}


@dataclass(slots=True, frozen=True)
class SchedulingWeights:
    """Immutable scheduling weight configuration."""

    critical_path: float
    request_count: float
    parallel_enablement: float
    depth_penalty: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchedulingWeights:
        """Create from config dict, using defaults for missing keys."""
        return cls(
            critical_path=float(
                data.get("critical_path", DEFAULT_WEIGHTS["critical_path"])
            ),
            request_count=float(
                data.get("request_count", DEFAULT_WEIGHTS["request_count"])
            ),
            parallel_enablement=float(
                data.get("parallel_enablement", DEFAULT_WEIGHTS["parallel_enablement"])
            ),
            depth_penalty=float(
                data.get("depth_penalty", DEFAULT_WEIGHTS["depth_penalty"])
            ),
        )

    @classmethod
    def defaults(cls) -> SchedulingWeights:
        """Create with default weights."""
        return cls.from_dict({})


@dataclass(slots=True, frozen=True)
class BatchRoutingConfig:
    """Batch routing configuration."""

    scheduling_weights: SchedulingWeights
    stale_claim_timeout: float = 300.0
    budget_cache_ttl: float = 60.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BatchRoutingConfig:
        """Create from config dict."""
        weights_data = data.get("scheduling_weights", {})
        model_tracking = data.get("model_tracking", {})
        budget_mgmt = data.get("budget_management", {})

        return cls(
            scheduling_weights=SchedulingWeights.from_dict(weights_data),
            stale_claim_timeout=float(model_tracking.get("stale_claim_timeout", 300.0)),
            budget_cache_ttl=float(budget_mgmt.get("cache_ttl_seconds", 60.0)),
        )

    @classmethod
    def defaults(cls) -> BatchRoutingConfig:
        """Create with default configuration."""
        return cls(scheduling_weights=SchedulingWeights.defaults())


def load_batch_routing_config(config_path: Path | None = None) -> BatchRoutingConfig:
    """
    Load batch routing config from stargate_config.yaml.

    Args:
        config_path: Path to config file (defaults to standard location)

    Returns:
        BatchRoutingConfig (defaults if file not found or section missing)
    """
    import yaml

    if config_path is None:
        # Standard location relative to stargate service
        config_path = (
            Path(__file__).parent.parent.parent.parent.parent
            / "config"
            / "stargate_config.yaml"
        )

    if not config_path.exists():
        logger.debug(f"Config file not found at {config_path}, using defaults")
        return BatchRoutingConfig.defaults()

    try:
        with open(config_path) as f:
            full_config = yaml.safe_load(f) or {}

        batch_routing_section = full_config.get("batch_routing", {})
        if not batch_routing_section:
            logger.debug("No batch_routing section in config, using defaults")
            return BatchRoutingConfig.defaults()

        config = BatchRoutingConfig.from_dict(batch_routing_section)
        logger.info(
            f"Loaded batch routing config: weights={config.scheduling_weights}, "
            f"stale_timeout={config.stale_claim_timeout}s"
        )
        return config

    except Exception as e:
        logger.warning(f"Failed to load batch routing config: {e}, using defaults")
        return BatchRoutingConfig.defaults()
