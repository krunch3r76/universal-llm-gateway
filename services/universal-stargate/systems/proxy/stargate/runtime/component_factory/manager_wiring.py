"""Manager wiring for StargateProxy shared runtime components.

This module isolates the one-time async wiring of TokenManager and
ParameterManager onto the proxy's shared HTTP client (and optional
resource-aware model load waiter). It is intentionally small and has no
intra-package dependencies so it can be imported without pulling in the
larger bootstrap modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ...proxy import StargateProxy

logger = get_logger(__name__)


async def configure_token_and_parameter_managers(proxy: StargateProxy) -> None:
    """Wire TokenManager and ParameterManager with the shared HTTP client.

    This must be called after the proxy's http_client has been created but
    before any request processing that requires token or parameter management.
    When a ResourceAwareModelManager is present its internal load waiter is
    also attached to the TokenManager so that token accounting can react to
    model load/unload events in an event-driven fashion.

    Args:
        proxy: The fully-constructed (but not yet request-ready) StargateProxy
            instance whose managers and client attributes will be mutated.

    Raises:
        RuntimeError: If proxy.http_client is still None (defensive guard).
    """
    if proxy.http_client is None:  # pragma: no cover - defensive
        raise RuntimeError("HTTP client must be initialized before managers")

    await proxy.token_manager.set_http_client(proxy.http_client)
    await proxy.parameter_manager.set_http_client(proxy.http_client)

    if proxy.resource_aware_model_manager:
        proxy.token_manager.set_load_waiter(
            proxy.resource_aware_model_manager._load_waiter  # noqa: SLF001 - intentional
        )
        logger.info("✅ TokenManager wired with ModelLoadWaiter (event-driven)")
