"""
Adaptive polling strategy based on gateway activity.

INVARIANT: Fast polling when busy, slow when idle
INVARIANT: Cooldown period after activity ends
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ....common.config.schema import FederationConfig, RemoteStargateConfig
    from ....master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )

logger = get_logger(__name__)


class AdaptivePollingStrategy:
    """
    Adapts polling interval based on gateway activity.

    States:
    - IDLE: No recent activity → slow polling (base interval)
    - BUSY: Active requests or busy models → fast polling
    - COOLDOWN: Recently active → fast polling (briefly)
    """

    def __init__(
        self,
        remote_config: RemoteStargateConfig,
        config: FederationConfig,
        gateway_manager: FederatedGatewayManager,
    ):
        self._remote_config = remote_config
        self._config = config
        self._gateway_manager = gateway_manager

        self._fast_poll_interval_ms = config.fast_poll_interval_ms
        self._fast_poll_cooldown_ms = config.fast_poll_cooldown_ms
        self._last_active_time: float = 0
        self._gateway_id: str | None = None

    def set_gateway_id(self, gateway_id: str) -> None:
        """Set gateway ID (learned from first response)."""
        self._gateway_id = gateway_id

    async def apply_startup_jitter(self) -> None:
        """Apply random jitter on startup to avoid thundering herd."""
        import asyncio

        base_interval_s = self._remote_config.telemetry_poll_interval_ms / 1000.0
        jitter = random.uniform(0, base_interval_s * 0.2)

        logger.debug(
            f"Startup jitter: sleeping {jitter:.2f}s "
            f"for {self._remote_config.stargate_id}"
        )
        await asyncio.sleep(jitter)

    def get_poll_interval(self) -> int:
        """Get poll interval based on current state."""
        if not self._gateway_id:
            return self._remote_config.telemetry_poll_interval_ms

        state = self._get_state()

        if state in ("BUSY", "COOLDOWN"):
            return self._fast_poll_interval_ms

        return self._remote_config.telemetry_poll_interval_ms

    def _get_state(self) -> str:
        """Determine current polling state."""
        gateway = self._gateway_manager.get_gateway(self._gateway_id)

        if gateway and (gateway.active_requests > 0 or len(gateway.busy_models) > 0):
            self._last_active_time = time.monotonic()
            return "BUSY"

        elapsed_ms = (time.monotonic() - self._last_active_time) * 1000

        if elapsed_ms < self._fast_poll_cooldown_ms:
            return "COOLDOWN"

        return "IDLE"
