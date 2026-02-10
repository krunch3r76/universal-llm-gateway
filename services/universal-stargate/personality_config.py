"""
Personality configuration management for different model types.

This module provides API-based personality configuration using gateway metadata.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from universal_logging import get_logger

from .gateway_client import GatewayClient, GatewayConfig
from .gateways import SingleGatewayManager

logger = get_logger(__name__)


@dataclass
class PersonalityProfile:
    """Personality profile for a model type"""

    preserve_personality: bool = True
    system_integration: str = "subtle"
    formatting_style: str = "minimal"
    personality_keywords: list[str] = field(default_factory=list)
    system_prompt_styles: dict[str, str] = field(default_factory=dict)
    generation_defaults: dict[str, Any] = field(default_factory=dict)
    custom_formatter: str | None = None


class PersonalityConfig:
    """
    Manages personality configurations for different model types.

    This class provides a centralized way to:
    - Get personality configurations from gateway API
    - Detect model personality types via gateway
    - Apply appropriate formatting rules from gateway data
    - Cache personality data for performance
    """

    def __init__(
        self,
        gateway_client: GatewayClient | None = None,
        gateway_manager: SingleGatewayManager | None = None,
    ):
        """
        Initialize personality configuration.

        Args:
            gateway_client: Gateway client for API-based configuration
            gateway_manager: Single gateway manager for setup
        """
        self.gateway_client = gateway_client
        self.gateway_manager = gateway_manager

        # API-based cache for model info dicts
        self._model_cache: dict[str, dict[str, Any]] = {}
        self._cache_timestamp = 0
        self._cache_ttl = 300  # 5 minutes

        self._api_enabled = True
        logger.info("PersonalityConfig initialized with gateway API support")

    async def get_model_info(self, model_id: str) -> dict[str, Any] | None:
        """Get model info dict from gateway API with caching.

        Returns raw dict with arbitrary fields for personality configuration.
        """
        # Check cache first
        if (
            time.time() - self._cache_timestamp
        ) < self._cache_ttl and model_id in self._model_cache:
            return self._model_cache[model_id]

        try:
            if self.gateway_client:
                info = await self.gateway_client.fetch_model_info_dict(model_id)
            elif self.gateway_manager:
                info = await self.gateway_manager.fetch_model_info_dict(model_id)
            else:
                logger.warning("No gateway client or manager available")
                return None

            if info:
                self._model_cache[model_id] = info
                self._cache_timestamp = time.time()

            return info
        except Exception as e:
            logger.error(f"Failed to get model info for {model_id}: {e}")
            return None

    async def get_personality_profile(self, model_id: str) -> PersonalityProfile | None:
        """Get personality profile for a model"""
        info = await self.get_model_info(model_id)
        if not info:
            return None

        # Extract personality information from model info dict
        personality_info = info.get("personality", {})

        return PersonalityProfile(
            preserve_personality=personality_info.get("preserve_personality", True),
            system_integration=personality_info.get("system_integration", "subtle"),
            formatting_style=personality_info.get("formatting_style", "minimal"),
            personality_keywords=personality_info.get("keywords", []),
            system_prompt_styles=personality_info.get("system_prompt_styles", {}),
            generation_defaults=personality_info.get("generation_defaults", {}),
            custom_formatter=personality_info.get("custom_formatter"),
        )

    async def get_system_prompt_style(
        self, model_id: str, style: str = "default"
    ) -> str | None:
        """Get system prompt template for a model and style"""
        profile = await self.get_personality_profile(model_id)
        if not profile:
            return None

        return profile.system_prompt_styles.get(style)

    async def get_generation_defaults(self, model_id: str) -> dict[str, Any]:
        """Get generation parameter defaults for a model"""
        profile = await self.get_personality_profile(model_id)
        if not profile:
            return {}

        return profile.generation_defaults.copy()

    def get_status(self) -> dict[str, Any]:
        """Get configuration status"""
        if not self._api_enabled:
            return {"api_enabled": False, "legacy_mode": True}

        status = {"api_enabled": True, "legacy_mode": False}
        if self.gateway_client:
            status["gateway_client"] = True
        if self.gateway_manager:
            status["gateway_manager"] = True
        status["cache_size"] = len(self._model_cache)
        status["cache_age"] = time.time() - self._cache_timestamp

        return status


async def create_api_personality_config(
    gateway_config: str | GatewayConfig | None = None,
    gateway_url: str | None = None,
) -> PersonalityConfig:
    """
    Create API-based personality configuration.

    Args:
        gateway_config: Gateway URL (string) or GatewayConfig object
        gateway_url: Single gateway URL (convenience parameter)

    Returns:
        PersonalityConfig instance with API support
    """
    # Import here to avoid circular dependency
    from systems.proxy.utils import _normalize_gateway_config

    # Normalize to single GatewayConfig (1:1 Stargate:Gateway relationship)
    config = _normalize_gateway_config(
        gateway_config=gateway_config,
        gateway_url=gateway_url,
        default_url=None,  # Require explicit configuration
    )

    # Single gateway per Stargate
    gateway_manager = SingleGatewayManager(gateway_config=config)
    await gateway_manager.initialize()
    return PersonalityConfig(gateway_manager=gateway_manager)


# Global instance - will be created based on usage
_global_personality_config: PersonalityConfig | None = None


def get_global_personality_config() -> PersonalityConfig | None:
    """Get the global personality configuration instance"""
    return _global_personality_config


async def initialize_global_personality_config(
    gateway_configs: list[str | GatewayConfig] | None = None,
    gateway_url: str | None = None,
) -> PersonalityConfig:
    """Initialize the global personality configuration"""
    global _global_personality_config

    _global_personality_config = await create_api_personality_config(
        gateway_configs=gateway_configs, gateway_url=gateway_url
    )

    return _global_personality_config
