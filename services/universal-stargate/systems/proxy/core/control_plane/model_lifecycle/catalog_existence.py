"""
Catalog existence checks for model routing.

Invariant: Catalog existence is a property of the canonical model ID.
"""

from typing import Any, Protocol

from model_id import ModelId
from universal_logging import get_logger

logger = get_logger(__name__)


class _GatewayClient(Protocol):
    async def fetch_model_configuration(self, model_id: str) -> Any: ...


class _GatewayConfig(Protocol):
    name: str


class _GatewayInstance(Protocol):
    client: _GatewayClient
    config: _GatewayConfig


class _GatewayManager(Protocol):
    def get_gateway(self) -> _GatewayInstance | None: ...


async def check_model_exists_anywhere(
    gateway_manager: _GatewayManager,
    model_id: ModelId,
) -> bool:
    """
    Check if model exists in gateway catalog.

    Note: Name is historical - with single gateway, this checks THE gateway.

    Args:
        gateway_manager: Gateway manager instance
        model_id: ModelId object (use .catalog_lookup_id for catalog queries)
    """
    gw = gateway_manager.get_gateway()
    if not gw:
        logger.debug(f"No gateway available to check model {model_id}")
        return False

    # Use synthetic_id (full ID with all suffixes) for gateway queries
    # Gateway accepts full IDs and handles -hybrid normalization internally
    # But -cpu suffix MUST be included for CPU models
    lookup_id = model_id.synthetic_id
    logger.debug(
        f"Checking if model {model_id} exists in {gw.config.name} catalog (lookup_id={lookup_id})"
    )

    try:
        config = await gw.client.fetch_model_configuration(lookup_id)
        if config:
            logger.info(f"✅ Model {model_id} found in {gw.config.name} catalog")
            return True
        logger.info(f"⚠️ Model {model_id} not found in {gw.config.name}")
    except Exception as e:
        logger.warning(f"❌ Error checking {model_id} in {gw.config.name}: {e}")

    return False
