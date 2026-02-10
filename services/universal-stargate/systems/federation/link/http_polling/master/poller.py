"""
HTTP telemetry poller lifecycle management.

INVARIANT: ∀ polling_remote: poll_task ∈ running_tasks
INVARIANT: Polling stops on shutdown (graceful cleanup)
INVARIANT: max_backoff ≤ 30000ms (FED-11)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
from universal_logging import get_logger

from ..validation import require_polling_mode
from .adaptive import AdaptivePollingStrategy
from .applicator import TelemetryApplicator
from .fetcher import TelemetryFetcher

if TYPE_CHECKING:
    from ....common.config.schema import FederationConfig, RemoteStargateConfig
    from ....master.manager.federated_gateway_manager import FederatedGatewayManager

logger = get_logger(__name__)


class HTTPPollingReceiver:
    """
    Polls telemetry from a single remote via HTTP.

    Implements TelemetryReceiver protocol.

    INVARIANT: One poller per polling remote
    INVARIANT: Publishes GATEWAY_RESOURCE_UPDATE after each update
    """

    def __init__(
        self,
        remote_config: RemoteStargateConfig,
        config: FederationConfig,
        gateway_manager: FederatedGatewayManager,
        event_bus: Any,
    ):
        require_polling_mode(remote_config)

        self._remote_config = remote_config
        self._config = config
        self._gateway_manager = gateway_manager
        self._event_bus = event_bus

        self._running = False
        self._poll_task: asyncio.Task[None] | None = None
        self._http_client: httpx.AsyncClient | None = None

        # Composed helpers (SRP)
        self._fetcher: TelemetryFetcher | None = None
        self._applicator: TelemetryApplicator | None = None
        self._adaptive: AdaptivePollingStrategy | None = None

    async def start(self) -> None:
        """Start polling loop as background task."""
        if self._running:
            return

        self._running = True

        # Initialize HTTP client with connection pooling
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
            limits=httpx.Limits(
                max_connections=self._config.http_pool.max_connections,
                max_keepalive_connections=self._config.http_pool.max_keepalive_connections,
            ),
        )

        # Initialize helpers
        self._fetcher = TelemetryFetcher(
            remote_config=self._remote_config,
            config=self._config,
            http_client=self._http_client,
        )
        self._applicator = TelemetryApplicator(
            gateway_manager=self._gateway_manager,
            event_bus=self._event_bus,
        )
        self._adaptive = AdaptivePollingStrategy(
            remote_config=self._remote_config,
            config=self._config,
            gateway_manager=self._gateway_manager,
        )

        # Start background task
        self._poll_task = asyncio.create_task(
            self._poll_loop(),
            name=f"http-telemetry-poller-{self._remote_config.stargate_id}",
        )
        self._poll_task.add_done_callback(self._on_poll_error)

        logger.info(
            f"Started HTTP polling for relay stargate "
            f"{self._remote_config.stargate_id} "
            f"(interval={self._remote_config.telemetry_poll_interval_ms}ms)"
        )

    async def stop(self) -> None:
        """Stop polling task gracefully."""
        if not self._running:
            return

        self._running = False

        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        logger.info(
            f"Stopped HTTP polling for relay stargate {self._remote_config.stargate_id}"
        )

    def is_running(self) -> bool:
        return self._running

    async def _poll_loop(self) -> None:
        """Main polling loop with adaptive interval."""
        # Startup jitter to avoid thundering herd
        await self._adaptive.apply_startup_jitter()

        while self._running:
            try:
                response = await self._fetcher.fetch()
                if response is not None:
                    await self._applicator.apply(response)

                    # Update adaptive strategy with gateway_id
                    if self._fetcher.last_gateway_id:
                        self._adaptive.set_gateway_id(self._fetcher.last_gateway_id)
                else:
                    # 204 No Content - heartbeat only
                    await self._applicator.send_heartbeat(self._fetcher.last_gateway_id)

                self._fetcher.reset_backoff()
                interval_ms = self._adaptive.get_poll_interval()

            except Exception as e:
                logger.error(f"Poll error for {self._remote_config.stargate_id}: {e}")
                interval_ms = self._fetcher.get_backoff_interval()
                self._fetcher.request_full_sync()

            await asyncio.sleep(interval_ms / 1000.0)

    def _on_poll_error(self, task: asyncio.Task) -> None:
        """Handle poll task completion/error."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error(
                f"Poll task failed for {self._remote_config.stargate_id}: {exc}"
            )
