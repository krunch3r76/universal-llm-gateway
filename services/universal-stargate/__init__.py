"""
Universal LLM Gateway Middleware Package

Modern API-based middleware operation for distributed deployment scenarios.

The middleware package is designed to be:
- Modular and reusable
- Exportable as a standalone package
- Compatible with different LLM gateway architectures
- Configurable for different deployment scenarios
- API-based for distributed deployment

API-BASED OPERATION:
- Middleware queries gateway API for all model metadata
- No direct file system access required
- Supports multiple gateway instances
- Automatic failover and load balancing
- Real-time configuration updates from gateway
"""

from __future__ import annotations

# middleware/
# ├── proxy/
# │   ├── __init__.py          # Package exports
# │   ├── app.py               # FastAPI app and endpoints
# │   ├── token_management/    # Token management functionality
# │   └── core/                # Core request processing
# ├── start_proxy.py          # Main entry point (used by service manager)
# └── scripts/
#     └── start-stargate.sh   # Service wrapper (recommended)
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from .gateway_client import GatewayClient, GatewayConfig, ModelMetadata
    from .gateways import SingleGatewayManager
    from .personality_config import (
        PersonalityConfig,
        create_api_personality_config,
        get_global_personality_config,
    )
    from .systems.transformations import OutputFormat, TransformationEngine

logger = get_logger(__name__)


def __getattr__(name: str) -> Any:
    """
    Lazily resolve package exports.

    This avoids import-time failures when test collection imports this module as a
    plain file (without package context) while preserving public package exports.
    """
    if name in {"GatewayClient", "GatewayConfig", "ModelMetadata"}:
        from .gateway_client import GatewayClient, GatewayConfig, ModelMetadata

        mapping = {
            "GatewayClient": GatewayClient,
            "GatewayConfig": GatewayConfig,
            "ModelMetadata": ModelMetadata,
        }
        return mapping[name]

    if name == "SingleGatewayManager":
        from .gateways import SingleGatewayManager

        return SingleGatewayManager

    if name in {
        "PersonalityConfig",
        "create_api_personality_config",
        "get_global_personality_config",
    }:
        from .personality_config import (
            PersonalityConfig,
            create_api_personality_config,
            get_global_personality_config,
        )

        mapping = {
            "PersonalityConfig": PersonalityConfig,
            "create_api_personality_config": create_api_personality_config,
            "get_global_personality_config": get_global_personality_config,
        }
        return mapping[name]

    if name in {"TransformationEngine", "OutputFormat"}:
        from .systems.transformations import OutputFormat, TransformationEngine

        mapping = {
            "TransformationEngine": TransformationEngine,
            "OutputFormat": OutputFormat,
        }
        return mapping[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_global_middleware():
    """Placeholder for missing function"""
    return None


__version__ = "2.0.0"
__all__ = [
    # Core components
    "PersonalityConfig",
    "TransformationEngine",
    "OutputFormat",
    # API-based components
    "GatewayClient",
    "GatewayConfig",
    "ModelMetadata",
    "SingleGatewayManager",
    # Factory functions
    "create_api_personality_config",
    "initialize_middleware",
    "MiddlewareManager",
    # Global accessors
    "get_global_middleware",
    "get_global_personality_config",
    # Package info
    "PACKAGE_INFO",
]


async def initialize_middleware(
    gateway_configs: list[str | GatewayConfig] | None = None,
    gateway_url: str | None = None,
) -> dict[str, Any]:
    """
    Initialize middleware for 1+ gateway operation.

    Args:
        gateway_configs: List of gateway URLs or GatewayConfig objects (preferred)
        gateway_url: Single gateway URL (convenience - will be converted to list)

    Returns:
        Dictionary containing initialized middleware components
    """
    from systems.proxy.utils import _normalize_gateway_config

    from .gateways import SingleGatewayManager
    from .personality_config import PersonalityConfig

    # Normalize to single GatewayConfig object (1:1 Stargate:Gateway relationship)
    gateway_config = _normalize_gateway_config(
        gateway_config=gateway_configs[0] if gateway_configs else None,
        gateway_url=gateway_url,
        default_url="http://localhost:8000",
    )

    # Create gateway manager (single gateway per Stargate)
    # Event-driven: no health_check_interval (WebSocket callbacks emit events)
    gateway_manager = SingleGatewayManager(gateway_config=gateway_config)
    await gateway_manager.initialize()

    # Create middleware components
    personality_config_instance = PersonalityConfig(gateway_manager=gateway_manager)

    logger.info(f"Middleware initialized with gateway: {gateway_config.name}")

    return {
        "mode": "api",
        "personality_config": personality_config_instance,
        "gateway_client": None,  # Deprecated - use gateway_manager
        "gateway_manager": gateway_manager,
    }


async def middleware_health_check(
    middleware_components: dict[str, Any],
) -> dict[str, Any]:
    """
    Perform health check on middleware components.

    Args:
        middleware_components: Components returned from initialize_middleware()

    Returns:
        Health status dictionary
    """
    mode = middleware_components["mode"]

    health_status = {"mode": mode, "healthy": True, "details": {}}

    try:
        if mode == "legacy":
            # Legacy health check
            personality_config = middleware_components["personality_config"]
            health_status["details"] = {
                "personality_config": "active",
                "profiles_loaded": len(personality_config.list_profiles()),
                "legacy_mode": True,
            }

        elif mode == "api":
            # API-based health check (single gateway)
            gateway_manager = middleware_components["gateway_manager"]
            # NOTE: Using truthiness check - only report gateway if connected
            # SingleGatewayManager.__bool__ returns True only when gateway connected
            if gateway_manager:
                gateway_status = gateway_manager.get_gateway_status()
                healthy_gateways = 1 if gateway_manager.get_gateway() else 0
                total_gateways = 1

                health_status["details"] = {
                    "gateway_manager": "active",
                    "healthy_gateways": healthy_gateways,
                    "total_gateways": total_gateways,
                    "gateway_status": gateway_status,
                }
                health_status["healthy"] = healthy_gateways > 0

    except Exception as e:
        health_status["healthy"] = False
        health_status["error"] = str(e)
        logger.error(f"Health check failed: {e}")

    return health_status


async def shutdown_middleware(middleware_components: dict[str, Any]) -> None:
    """
    Gracefully shutdown middleware components.

    Args:
        middleware_components: Components returned from initialize_middleware()
    """
    mode = middleware_components["mode"]

    try:
        if mode == "api":
            # Shutdown gateway manager (1+ gateways)
            gateway_manager = middleware_components["gateway_manager"]
            # CRITICAL: Use 'is not None' - SingleGatewayManager.__bool__ returns False
            # when gateway not connected, but we should still attempt shutdown
            if gateway_manager is not None:
                await gateway_manager.shutdown()

        logger.info(f"Middleware shutdown complete (mode: {mode})")

    except Exception as e:
        logger.error(f"Error during middleware shutdown: {e}")


class MiddlewareManager:
    """
    High-level middleware manager for easy initialization and management.

    This class provides a simple interface for managing middleware lifecycle
    in different deployment scenarios.
    """

    def __init__(self):
        self.components: dict[str, Any] | None = None
        self._initialized = False

    async def initialize(
        self,
        gateway_configs: list[str | GatewayConfig] | None = None,
        gateway_url: str | None = None,
    ) -> None:
        """
        Initialize middleware for 1+ gateway operation.

        Args:
            gateway_configs: List of gateway URLs or GatewayConfig objects (preferred)
            gateway_url: Single gateway URL (convenience parameter)
        """
        if self._initialized:
            await self.shutdown()

        self.components = await initialize_middleware(gateway_configs, gateway_url)
        self._initialized = True

        # Get count for logging
        if gateway_configs:
            count = len(gateway_configs)
        elif gateway_url:
            count = 1
        else:
            count = 1  # Default fallback

        logger.info(f"MiddlewareManager initialized with {count} gateway(s)")

    async def health_check(self) -> dict[str, Any]:
        """Perform health check"""
        if not self._initialized or not self.components:
            return {"healthy": False, "error": "Not initialized"}

        return await middleware_health_check(self.components)

    async def shutdown(self) -> None:
        """Shutdown middleware"""
        if self._initialized and self.components:
            await shutdown_middleware(self.components)
            self.components = None
            self._initialized = False

    def get_personality_config(self) -> PersonalityConfig | None:
        """Get personality configuration instance"""
        if self.components:
            return self.components.get("personality_config")
        return None

    def is_initialized(self) -> bool:
        """Check if middleware is initialized"""
        return self._initialized

    def get_mode(self) -> str | None:
        """Get current operation mode"""
        if self.components:
            return self.components.get("mode")
        return None


# Package metadata for potential export
PACKAGE_INFO = {
    "name": "universal-llm-gateway-middleware",
    "version": __version__,
    "description": (
        "Middleware components for LLM gateway personality preservation and "
        "chat formatting with API-based distributed deployment support"
    ),
    "author": "krunch3r76",
    "license": "MIT",
    "python_requires": ">=3.12",
    "dependencies": [
        "pydantic>=2.0",
        "httpx>=0.24.0",
        "pyyaml>=6.0",
        "typing-extensions>=4.0",
    ],
    "features": [
        "API-based distributed deployment",
        "Federation-based distributed routing across Stargates",
        "Real-time configuration from gateway API",
        "Backward compatibility with file-based operation",
        "Conservative personality preservation",
        "Automatic chat template detection",
        "Dynamic parameter defaults",
    ],
    "deployment_modes": [
        "api",  # API-based operation with 1+ gateways
        "legacy",  # File-based operation (deprecated)
    ],
}
