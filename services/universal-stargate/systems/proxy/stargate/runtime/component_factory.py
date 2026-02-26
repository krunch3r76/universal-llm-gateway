from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

from systems.routing.selection.catalog import collect_stargate_model_sets

from ....profiles import ProfileConfigLoader, ProfileManager
from ....transformations import TransformationConfigLoader, TransformationEngine
from ...core.nonstreaming import RequestExecutor, RequestForwarder, RequestPreparer
from ...core.streaming import StreamHandler

if TYPE_CHECKING:
    from ..proxy import StargateProxy

logger = get_logger(__name__)


async def configure_token_and_parameter_managers(proxy: StargateProxy) -> None:
    """Wire TokenManager and ParameterManager with the shared http client."""
    if proxy.http_client is None:  # pragma: no cover - defensive
        raise RuntimeError("HTTP client must be initialized before managers")

    await proxy.token_manager.set_http_client(proxy.http_client)
    await proxy.parameter_manager.set_http_client(proxy.http_client)

    if proxy.resource_aware_model_manager:
        proxy.token_manager.set_load_waiter(
            proxy.resource_aware_model_manager._load_waiter  # noqa: SLF001 - intentional
        )
        logger.info("✅ TokenManager wired with ModelLoadWaiter (event-driven)")


def initialize_transformation_engine(config_dir: Path) -> TransformationEngine:
    """
    Initialize TransformationEngine at startup.

    Args:
        config_dir: Path to config directory containing model_transformations.yaml

    Returns:
        Configured TransformationEngine
    """
    config_path = config_dir / "model_transformations.yaml"
    try:
        config_loader = TransformationConfigLoader(config_path)
        engine = TransformationEngine(config_loader=config_loader)
        logger.info("TransformationEngine initialized")
        return engine
    except FileNotFoundError:
        logger.warning(f"No transformation config at {config_path}, using empty config")

        # Create empty loader for no-config case
        class EmptyConfigLoader:
            def get_for_model(self, model):
                return None

        return TransformationEngine(config_loader=EmptyConfigLoader())


def initialize_profile_manager(config_dir: Path) -> ProfileManager:
    """
    Initialize ProfileManager at startup.

    Args:
        config_dir: Path to config directory containing profiles.yaml

    Returns:
        Configured ProfileManager

    Raises:
        FileNotFoundError: If profiles.yaml doesn't exist (fail-fast, no fallback)
    """
    config_path = config_dir / "profiles.yaml"
    # Fail-fast: profiles.yaml is required, no empty fallback
    config_loader = ProfileConfigLoader(config_path)
    manager = ProfileManager(config_loader=config_loader)
    logger.info("ProfileManager initialized")
    return manager


def create_token_allocation_policy(proxy: StargateProxy):
    """
    Create token allocation policy from proxy state.

    Extracts from token_manager if available (execution-capable),
    or from config for router-only.

    Returns:
        TokenAllocationPolicy or None if not needed

    Raises:
        RuntimeError: If token_manager missing required attributes
    """
    from ...core.nonstreaming.token_management import TokenAllocationPolicy

    # Only needed if federation is configured (Master mode)
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
    """Initialize modular request handling components."""
    if proxy.gateway_manager is None:  # pragma: no cover - defensive
        raise RuntimeError("Gateway manager must be initialized before requests")

    # Get config directory
    config_dir = (
        Path(proxy.config.config_path).parent
        if proxy.config.config_path
        else Path("config")
    )

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

    Returns:
        Tuple of (forward_func, streaming_forward_func) that fail fast
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


def initialize_router_only_request_components(proxy: StargateProxy) -> None:
    """
    Initialize request components for router-only Master.

    Router-only mode:
    - No local gateway → no local forwarding
    - All requests routed to federated remotes
    - Token counting via federation forwarder (on execution target)

    INVARIANT:
        ∀ request:
            token_counting_target = execution_target
            execution_target = selected_remote_stargate.gateway
            ¬∃ local_gateway ⟹ token_counting via federation_forwarder ONLY
    """
    # Get config directory
    config_dir = (
        Path(proxy.config.config_path).parent
        if proxy.config.config_path
        else Path("config")
    )

    # Initialize transformation engine (startup I/O)
    transformation_engine = initialize_transformation_engine(config_dir)

    # Initialize profile manager (startup I/O) - stored on proxy for DI access
    proxy.profile_manager = initialize_profile_manager(config_dir)

    # Request preparer works without gateway (prepares request for routing)
    # Router-only detected via gateway_manager=None (single source of truth)
    proxy.request_preparer = RequestPreparer(
        gateway_manager=None,  # No local gateway → is_router_only=True
        transformation_engine=transformation_engine,
        profile_manager=proxy.profile_manager,
        token_manager=None,  # No local token manager
        token_management_enabled=False,  # Disabled for router-only
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

    logger.info("✅ Router-only components initialized (federated token counting)")


async def initialize_hot_reload(proxy: StargateProxy) -> None:
    """Initialize hot-reload watchers for profiles and pipelines."""
    from universal_hot_reload import HotReloadWatcher

    # Use public proxy.profile_manager (no private attribute reach-through)
    profile_manager = proxy.profile_manager

    # Check if profile_manager is initialized
    if profile_manager is None:
        logger.warning("ProfileManager not initialized, skipping profile hot-reload")
        proxy.profile_watcher = None
        return  # Exit early if not initialized

    async def reload_profiles(_file_path: str):
        """Reload profiles when config file changes (non-blocking)."""
        # CRITICAL: Use to_thread to avoid blocking event loop with sync I/O
        await asyncio.to_thread(profile_manager.reload_profiles)

    proxy.profile_watcher = HotReloadWatcher(
        name="profiles",
        watch_path=profile_manager.profiles_path,
        on_change=reload_profiles,
        debounce_ms=1000,
        recursive=False,
        patterns=[".yaml"],
    )

    if await proxy.profile_watcher.start():
        logger.info("✅ Profile hot-reload active")
    else:
        logger.warning("Profile hot-reload not started")
        proxy.profile_watcher = None


def create_catalog_provider(
    proxy: StargateProxy,
) -> Callable[[], list[set[str]]]:
    """
    Create catalog provider closure for PipelineRegistry.

    CRITICAL: Accesses proxy.federated_manager at call time, not capture time.
    Federation is initialized AFTER pipeline system, so the property
    returns None during PipelineRegistry construction but returns the
    actual manager when the provider is called later.

    Args:
        proxy: StargateProxy instance (captured by closure)

    Returns:
        Callable that returns list[set[str]] of model sets from all sources
    """

    def provider() -> list[set[str]]:
        return collect_stargate_model_sets(
            proxy.gateway_manager,
            proxy.federated_manager,  # Accessed when called, not when created
        )

    return provider


async def _emit_pipeline_unavailable_events(proxy: StargateProxy) -> None:
    """Emit pipeline.registry.unavailable for each permanently-skipped pipeline.

    Called after load() and reload_pipelines() so operators can query the event
    stream for pipelines that cannot start due to missing model dependencies.
    ∀ (pipeline_id, missing) ∈ registry.unavailable_pipelines: signal emitted once.
    """
    if proxy.event_bus is None or proxy.pipeline_registry is None:
        return

    from src.scheduling.events import PipelineRegistryUnavailable

    for pipeline_id, missing_models in proxy.pipeline_registry.unavailable_pipelines:
        event = PipelineRegistryUnavailable(
            pipeline_id=pipeline_id,
            missing_models=missing_models,
        )
        await proxy.event_bus.publish_async_nowait(event)


def _subscribe_pipeline_reload_on_catalog_change(proxy: StargateProxy) -> None:
    """
    Subscribe to federation catalog changes for pipeline reload.

    INVARIANT: reload_pipelines() runs off-thread to avoid blocking event loop
    (it performs sync filesystem I/O).

    Args:
        proxy: StargateProxy with federation_integration and event_bus
    """
    if proxy.federation_integration is None or proxy.event_bus is None:
        return

    from src.scheduling.events import FEDERATION_GATEWAY_CATALOG_CHANGED

    async def on_catalog_changed(event) -> None:
        """Reload pipelines when federation gateway catalog changes."""
        if proxy.pipeline_registry is None:
            return

        try:
            # Run sync I/O off-thread to avoid blocking event loop
            # reload_pipelines() returns (old_count, new_count) atomically
            old_count, new_count = await asyncio.to_thread(
                proxy.pipeline_registry.reload_pipelines
            )

            if new_count != old_count:
                gateway_id = event.payload.get("gateway_id", "unknown")
                logger.info(
                    f"🔄 Pipelines reloaded after catalog change from {gateway_id}: "
                    f"{old_count} → {new_count} pipelines"
                )

            await _emit_pipeline_unavailable_events(proxy)
        except Exception as e:
            logger.error(f"Failed to reload pipelines after catalog change: {e}")

    proxy.event_bus.subscribe_async(
        FEDERATION_GATEWAY_CATALOG_CHANGED, on_catalog_changed
    )
    logger.info("📦 Subscribed to federation catalog changes for pipeline reload")


async def initialize_pipeline_system(proxy: StargateProxy) -> None:
    """Initialize the pipeline registry/executor if available."""
    try:
        from systems.pipeline.executor import PipelineExecutor
        from systems.pipeline.registry import PipelineRegistry
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("Pipeline system not available: %s", exc)
        proxy.pipeline_registry = None
        proxy.pipeline_executor = None
        return

    try:
        # Get pipeline config from stargate config
        pipelines_config = proxy.config.get_pipelines_config()

        # Use project root as base directory for resolving relative search paths.
        # Priority:
        #   1. STARGATE_PROJECT_ROOT env var — set by service manager, always correct
        #   2. parent.parent of config_path — works for project_root/config/ layout
        #   3. cwd fallback
        if project_root_env := os.environ.get("STARGATE_PROJECT_ROOT"):
            config_base_dir = Path(project_root_env).resolve()
        elif proxy.config.config_path:
            # Assumes config at {project_root}/config/stargate_config.yaml
            config_base_dir = Path(proxy.config.config_path).parent.parent.resolve()
        else:
            config_base_dir = Path.cwd()

        # Load user handlers from all search paths (colocated with pipelines)
        from systems.pipeline.user_handlers import load_user_handlers

        search_paths = pipelines_config.get("search_paths", ["config"])
        total_loaded = 0
        for search_path in search_paths:
            try:
                # Resolve relative paths relative to config file directory
                expanded = Path(search_path).expanduser()
                if not expanded.is_absolute():
                    resolved_path = (config_base_dir / expanded).resolve()
                else:
                    resolved_path = expanded.resolve()

                loaded_count = load_user_handlers(config_base_dir=resolved_path)
                total_loaded += loaded_count
            except Exception as e:
                logger.warning(f"Failed to load handlers from {search_path}: {e}")

        if total_loaded > 0:
            logger.info(
                f"✅ Loaded {total_loaded} domain handler package(s) "
                f"from all search paths"
            )

        proxy.pipeline_registry = PipelineRegistry(
            search_paths=search_paths,
            get_gateway_catalogs=create_catalog_provider(proxy),
            config_defaults=pipelines_config.get("defaults", {}),
            config_base_dir=config_base_dir,
        )

        # Wire pipeline registry to gateway manager (if local gateway exists)
        if proxy.gateway_manager is not None:
            proxy.gateway_manager.pipeline_registry = proxy.pipeline_registry

        proxy.pipeline_registry.load()
        await _emit_pipeline_unavailable_events(proxy)

        # Reload pipelines if local gateway is already connected
        # (handles case where gateway connected before pipeline system initialized)
        if proxy.gateway_manager is not None:
            healthy_gateway = proxy.gateway_manager.get_gateway()
            if healthy_gateway:
                # reload_pipelines() returns (old_count, new_count)
                _old, _new = proxy.pipeline_registry.reload_pipelines()
                logger.info(
                    f"🔄 Pipelines reloaded after initialization: {_old} → {_new} "
                    "(local gateway already connected)"
                )
                await _emit_pipeline_unavailable_events(proxy)
        else:
            # Master mode (no local gateway): subscribe to federation catalog
            # changes for pipeline reload. Only Edge has gateway_manager (Stargate
            # + Gateway colocated in same container).
            _subscribe_pipeline_reload_on_catalog_change(proxy)

        proxy.pipeline_executor = PipelineExecutor(
            registry=proxy.pipeline_registry,
            request_executor=proxy.request_executor,
            proxy=proxy,
        )

        # Initialize pipeline hot-reload
        hot_reload_config = pipelines_config.get("hot_reload", {})
        if hot_reload_config.get("enabled", False):
            from systems.pipeline.hot_reload import PipelineHotReload

            proxy.pipeline_hot_reload = PipelineHotReload(
                registry=proxy.pipeline_registry,
                debounce_ms=hot_reload_config.get("debounce_ms", 2000),
                enabled=True,
            )

            if await proxy.pipeline_hot_reload.start():
                logger.info("🔥 Pipeline hot-reload monitoring active")
            else:
                logger.warning("Failed to start pipeline hot-reload")
                proxy.pipeline_hot_reload = None
        else:
            proxy.pipeline_hot_reload = None
            logger.info("Pipeline hot-reload disabled in configuration")

        search_paths = pipelines_config.get("search_paths", ["config"])
        logger.info(
            "✅ Pipeline execution system initialized from paths: %s", search_paths
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Pipeline system not available: %s", exc)
        proxy.pipeline_registry = None
        proxy.pipeline_executor = None
        proxy.pipeline_hot_reload = None
