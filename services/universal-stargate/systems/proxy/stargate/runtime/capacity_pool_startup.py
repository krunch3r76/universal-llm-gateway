from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ..proxy import StargateProxy

logger = get_logger(__name__)


def initialize_capacity_pool(proxy: StargateProxy) -> None:
    """Initialize and attach the Stargate request capacity pool before federation."""
    from systems.routing.capacity.pool import CapacityPool

    capacity_pool_config = proxy.config.get_capacity_pool_config()
    capacity_pool = CapacityPool(
        event_bus=proxy.event_bus,
        max_queue_depth=capacity_pool_config["max_queue_depth"],
    )
    logger.info(
        "✅ CapacityPool initialized (max_queue_depth=%d)",
        capacity_pool_config["max_queue_depth"],
    )

    proxy.capacity_pool = capacity_pool
