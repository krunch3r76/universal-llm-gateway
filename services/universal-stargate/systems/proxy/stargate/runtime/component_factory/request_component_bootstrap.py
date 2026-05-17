"""Request component bootstrap for local-gateway and Master/router-only modes.

This module owns construction of the request-processing pipeline pieces for both
execution-capable (local gateway) and router-only (Master/federation) Stargate
deployments:

- RequestPreparer (applies profiles, transformations, token accounting)
- RequestForwarder and StreamHandler (local HTTP paths when a gateway exists)
- RequestExecutor (the central orchestrator, with or without guards)
- TokenAllocationPolicy (derived from TokenManager or config for federated cases)
- StickyPlacementTracker (only in Master mode for routing hysteresis)
- Fail-fast local-forward guard callables (Master mode only)

ProfileManager and TransformationEngine are initialized here for both modes
via the sibling profile_transformation_bootstrap module. This keeps the
"config dir -> profile/transform objects" logic in one place while still
allowing request bootstrap to stay self-contained.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from ....core.nonstreaming import RequestExecutor, RequestForwarder, RequestPreparer
from ....core.streaming import StreamHandler

if TYPE_CHECKING:
    from ...proxy import StargateProxy

from .profile_transformation_bootstrap import (
    _get_config_dir,
    initialize_profile_manager,
    initialize_transformation_engine,
)

logger = get_logger(__name__)


def create_token_allocation_policy(proxy: StargateProxy):
    """
    Create token allocation policy from proxy state.

    Extracts from token_manager if available (execution-capable local gateway),
    or from config for router-only / Master deployments.

    The policy is only required when federation_forwarder exists; otherwise
    local token accounting is handled inside the gateway path and None is
    returned.

    Returns:
        TokenAllocationPolicy or None if not needed (no federation)

    Raises:
        RuntimeError: If token_manager is present but missing required attributes
            (defensive; indicates TokenManager mis-configuration).
    """
    from ....core.nonstreaming.token_management import TokenAllocationPolicy

    # Only needed if federation is configured (Master mode or federated edge)
    if not proxy.federation_forwarder:
        return None

    if proxy.token_manager:
        # Execution-capable: extract from token_manager
        try:
            return TokenAllocationPolicy.from_token_manager(proxy.token_manager)
        except AttributeError as e:
            logger.error(
                f"Failed to extract token allocation policy from TokenManager: {e}"
            )
            raise RuntimeError(
                "TokenManager missing completion_safety_buffer attribute"
            ) from e
    else:
        # Router-only: create from config
        policy = TokenAllocationPolicy.from_config(proxy.config)
        if policy.safety_buffer == 0:
            logger.info(
                "ℹ️ Token allocation policy: safety_buffer=0 (default). "
                "Set token_management.safety_buffer in config for production use."
            )
        return policy


def initialize_request_components(proxy: StargateProxy) -> None:
    """Initialize modular request handling components for local-gateway mode.

    This path is taken when Stargate has a direct gateway_manager (Edge or
    single-node deployments). All request paths are local; federation_forwarder
    may still be present for hybrid routing.

    Side effects:
        - Sets proxy.profile_manager, request_preparer, request_forwarder,
          stream_handler, request_executor, (optionally) token_allocation_policy
        - Performs filesystem I/O to load profiles.yaml and transformations.

    Raises:
        RuntimeError: If gateway_manager is absent (caller error).
    """
    if proxy.gateway_manager is None:  # pragma: no cover - defensive
        raise RuntimeError("Gateway manager must be initialized before requests")

    config_dir = _get_config_dir(proxy.config)

    # Initialize transformation engine (startup I/O)
    transformation_engine = initialize_transformation_engine(config_dir)

    # Initialize profile manager (startup I/O) - stored on proxy for DI access
    proxy.profile_manager = initialize_profile_manager(config_dir)

    proxy.request_preparer = RequestPreparer(
        gateway_manager=proxy.gateway_manager,
        transformation_engine=transformation_engine,
        profile_manager=proxy.profile_manager,
        token_manager=proxy.token_manager,
        token_management_enabled=proxy.token_management_enabled,
        config=proxy.config,
    )

    proxy.request_forwarder = RequestForwarder(
        gateway_url=proxy.gateway_url,
        http_client=proxy.http_client,
        config=proxy.config,
    )

    proxy.stream_handler = StreamHandler(
        gateway_url=proxy.gateway_url,
        http_client=proxy.http_client,
        config=proxy.config,
        monitor=proxy.monitor,
    )

    # Create token allocation policy for federation (if configured)
    token_allocation_policy = create_token_allocation_policy(proxy)

    proxy.request_executor = RequestExecutor(
        gateway_url=proxy.gateway_url,
        monitor=proxy.monitor,
        forward_request_func=proxy.forward_request,
        forward_streaming_request_func=proxy.forward_streaming_request,
        gateway_manager=proxy.gateway_manager,
        http_client=proxy.http_client,
        token_manager=proxy.token_manager,
        model_manager=proxy.resource_aware_model_manager,
        event_bus=proxy.event_bus,
        federation_forwarder=proxy.federation_forwarder,
        federation_circuit_breaker=proxy.federation_circuit_breaker,
        token_allocation_policy=token_allocation_policy,
        federated_manager=proxy.federated_manager,
        federated_load_orchestrator=proxy.federated_load_orchestrator,
        transformation_engine=transformation_engine,
        federation_integration=getattr(proxy, "federation_integration", None),
        capacity_pool=getattr(proxy, "capacity_pool", None),
    )


def _create_local_forward_guards():
    """
    Create guard functions that raise if local forwarding is attempted.

    Used exclusively in Master/router-only mode where no local gateway exists.
    Any attempt to call the local forward paths is a programmer error and must
    fail fast with a clear message.

    Returns:
        Tuple of (forward_func, streaming_forward_func) that fail fast with
        descriptive RuntimeError.
    """

    async def _raise_local_forward_error(*args, **kwargs):
        raise RuntimeError(
            "BUG: Local forward_request called in router-only mode. "
            "This should never happen - all requests must use federation."
        )

    async def _raise_local_streaming_error(*args, **kwargs):
        raise RuntimeError(
            "BUG: Local forward_streaming_request called in router-only mode. "
            "This should never happen - all requests must use federation."
        )

    return _raise_local_forward_error, _raise_local_streaming_error


def initialize_master_request_components(proxy: StargateProxy) -> None:
    """
    Initialize request components for Master (no local gateway).

    Master mode:
    - No local gateway -> no local forwarding
    - All requests routed to federated remotes
    - Client-facing policy (profiles, system prompts) applied locally
    - Token counting via federation forwarder (on execution target)

    INVARIANT:
        ∀ request:
            token_counting_target = execution_target
            execution_target = selected_remote_stargate.gateway
            ¬∃ local_gateway ⟹ token_counting via federation_forwarder ONLY

    Side effects:
        - Sets proxy.profile_manager, request_preparer, request_executor,
          stability_tracker, routing_config
        - request_forwarder and stream_handler are explicitly set to None
        - Performs filesystem I/O for profiles + transformations
    """
    config_dir = _get_config_dir(proxy.config)

    # Initialize transformation engine (startup I/O)
    transformation_engine = initialize_transformation_engine(config_dir)

    # Initialize profile manager (startup I/O) - stored on proxy for DI access
    proxy.profile_manager = initialize_profile_manager(config_dir)

    # Master preparer: no local gateway, applies client-facing policy (profiles)
    proxy.request_preparer = RequestPreparer(
        gateway_manager=None,  # No local gateway -> Master mode
        transformation_engine=transformation_engine,
        profile_manager=proxy.profile_manager,
        token_manager=None,
        token_management_enabled=False,
        config=proxy.config,
    )

    # Create stability tracker for routing hysteresis (process lifetime)
    from systems.routing.selection.decision import StickyPlacementTracker

    proxy.stability_tracker = StickyPlacementTracker()
    logger.info("✅ StickyPlacementTracker initialized for routing stability")

    # Store full config for routing policy (INV-1: must be full dict)
    proxy.routing_config = (
        proxy.config.config if hasattr(proxy.config, "config") else {}
    )

    # Create token allocation policy for federation
    token_allocation_policy = create_token_allocation_policy(proxy)

    # Create guard functions that fail if local forwarding attempted
    forward_guard, streaming_guard = _create_local_forward_guards()

    # Request executor with token allocation policy for federated token counting
    proxy.request_executor = RequestExecutor(
        gateway_url="",  # Placeholder - no local gateway
        monitor=proxy.monitor,
        forward_request_func=forward_guard,
        forward_streaming_request_func=streaming_guard,
        gateway_manager=None,  # No local gateway
        http_client=None,  # No local HTTP client
        token_manager=None,  # No local token manager
        model_manager=None,  # No local model manager
        event_bus=proxy.event_bus,
        federation_forwarder=proxy.federation_forwarder,
        federation_circuit_breaker=proxy.federation_circuit_breaker,
        token_allocation_policy=token_allocation_policy,
        federated_manager=proxy.federated_manager,
        federated_load_orchestrator=proxy.federated_load_orchestrator,
        routing_config=proxy.routing_config,
        stability_tracker=proxy.stability_tracker,
        transformation_engine=transformation_engine,
        federation_integration=proxy.federation_integration,
        capacity_pool=getattr(proxy, "capacity_pool", None),
    )

    # No request forwarder or stream handler (no local gateway)
    proxy.request_forwarder = None
    proxy.stream_handler = None

    logger.info("✅ Master request components initialized")
