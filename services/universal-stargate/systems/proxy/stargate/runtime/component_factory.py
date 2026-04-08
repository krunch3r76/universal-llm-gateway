from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from systems.routing.selection.catalog import get_all_available_models

from ....persona_aliases.manager import PersonaAliasManager
from ....profiles import ProfileConfigLoader, ProfileManager
from ....transformations import TransformationConfigLoader, TransformationEngine
from ...core.nonstreaming import RequestExecutor, RequestForwarder, RequestPreparer
from ...core.streaming import StreamHandler

if TYPE_CHECKING:
    from collections.abc import Callable

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

    Profiles are merged from the repo base and config_dir override: the repo
    config is always loaded as the base; config_dir/profiles.yaml overrides
    individual entries by name when present.

    Args:
        config_dir: Path to config directory (e.g. ~/.gateway)

    Returns:
        Configured ProfileManager
    """
    config_path = config_dir / "profiles.yaml"
    config_loader = ProfileConfigLoader(config_path)
    manager = ProfileManager(config_loader=config_loader)
    logger.info("ProfileManager initialized")
    return manager


def initialize_persona_alias_manager(config_dir: Path) -> PersonaAliasManager:
    """
    Initialize the PersonaAliasManager at startup.

    Persona aliases are loaded from config_dir/persona_aliases.yaml.
    This file is user-local and intentionally not bundled in the repo.
    """
    manager = PersonaAliasManager.load_from_config_dir(config_dir)
    logger.info("PersonaAliasManager initialized")
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


def _get_config_dir(config: Any) -> Path:
    """Resolve config directory from config_path."""
    return Path(config.config_path).parent if config.config_path else Path("config")


def initialize_request_components(proxy: StargateProxy) -> None:
    """Initialize modular request handling components."""
    if proxy.gateway_manager is None:  # pragma: no cover - defensive
        raise RuntimeError("Gateway manager must be initialized before requests")

    config_dir = _get_config_dir(proxy.config)

    # Initialize transformation engine (startup I/O)
    transformation_engine = initialize_transformation_engine(config_dir)

    # Initialize profile manager (startup I/O) - stored on proxy for DI access
    proxy.profile_manager = initialize_profile_manager(config_dir)
    proxy.persona_alias_manager = initialize_persona_alias_manager(config_dir)

    proxy.request_preparer = RequestPreparer(
        gateway_manager=proxy.gateway_manager,
        transformation_engine=transformation_engine,
        profile_manager=proxy.profile_manager,
        persona_alias_manager=proxy.persona_alias_manager,
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
    """
    config_dir = _get_config_dir(proxy.config)

    # Initialize transformation engine (startup I/O)
    transformation_engine = initialize_transformation_engine(config_dir)

    # Initialize profile manager (startup I/O) - stored on proxy for DI access
    proxy.profile_manager = initialize_profile_manager(config_dir)
    proxy.persona_alias_manager = initialize_persona_alias_manager(config_dir)

    # Master preparer: no local gateway, applies client-facing policy (profiles)
    proxy.request_preparer = RequestPreparer(
        gateway_manager=None,  # No local gateway -> Master mode
        transformation_engine=transformation_engine,
        profile_manager=proxy.profile_manager,
        persona_alias_manager=proxy.persona_alias_manager,
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
        try:
            await asyncio.to_thread(profile_manager.reload_profiles)
        except Exception as e:
            logger.error("Failed to hot-reload profiles: %s", e)

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


def create_model_checker(
    proxy: StargateProxy,
) -> Callable[[str], bool]:
    """Build per-model availability predicate for PipelineRegistry.

    Reads proxy state at call time (not capture time) so catalog data is current.

    A model ID is available when:
    1. It is a registered pipeline virtual ID, OR
    2. It appears in ``get_all_available_models`` (full union catalog from all
       gateways — same data as ``GET /v1/models/{model_id}`` with activation=all)

    If no gateway is connected yet, the catalog is empty and pipelines won't
    register.  Stargate triggers ``reload_pipelines()`` once catalogs arrive
    from edge gateways.
    """

    def checker(model_id: str) -> bool:
        reg = proxy.pipeline_registry
        if reg is not None and model_id in reg.pipelines:
            return True
        return model_id in get_all_available_models(
            proxy.gateway_manager,
            proxy.federated_manager,
        )

    return checker


async def _emit_pipeline_unavailable_events(proxy: StargateProxy) -> None:
    """Emit pipeline.registry.unavailable for each permanently-skipped pipeline.

    Called after load() and reload_pipelines() so operators can query the event
    stream for pipelines that cannot start due to missing model dependencies.
    ∀ (pipeline_id, missing) ∈ registry.unavailable_pipelines: signal emitted once.
    """
    if proxy.event_bus is None or proxy.pipeline_registry is None:
        return

    from src.scheduling.events import pipeline_registry_unavailable

    for pipeline_id, missing_models in proxy.pipeline_registry.unavailable_pipelines:
        event = pipeline_registry_unavailable(
            pipeline_id=pipeline_id,
            missing_models=missing_models,
        )
        try:
            await proxy.event_bus.publish_async_nowait(event)
        except Exception as e:
            logger.error(
                "Failed to publish pipeline unavailable event for %s: %s",
                pipeline_id,
                e,
            )


def _subscribe_pipeline_reload_on_gateway_connected(proxy: StargateProxy) -> None:
    """Subscribe to local gateway connect events for pipeline reload (Edge mode).

    When the gateway WebSocket connects after startup, the catalog data becomes
    available and pipelines that were loaded optimistically should be revalidated.

    INVARIANT: reload_pipelines() runs off-thread to avoid blocking event loop.
    """
    if proxy.event_bus is None:
        return

    from src.scheduling.events import GATEWAY_STATE_CHANGED

    async def on_gateway_state(event) -> None:
        if proxy.pipeline_registry is None:
            return
        if event.payload.get("connectivity") != "reachable":
            return
        try:
            old_count, new_count = await asyncio.to_thread(
                proxy.pipeline_registry.reload_pipelines
            )
            proxy.pipeline_catalog_synced = True
            logger.info(
                "🔄 Pipelines reloaded after gateway connected: %d → %d pipelines",
                old_count,
                new_count,
            )
        except Exception:
            logger.exception("Pipeline reload failed after gateway connect")

    proxy.event_bus.subscribe_async(GATEWAY_STATE_CHANGED, on_gateway_state)
    logger.debug("Subscribed to GATEWAY_STATE_CHANGED for pipeline reload")


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
            proxy.pipeline_catalog_synced = True

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


def _subscribe_aggregate_model_availability(proxy: StargateProxy) -> None:
    """Subscribe catalog-related signals to reconcile aggregate model availability."""
    emitter = getattr(proxy, "aggregate_availability_emitter", None)
    if proxy.event_bus is None or emitter is None:
        return

    from src.scheduling.events import (
        FEDERATION_GATEWAY_CATALOG_CHANGED,
        GATEWAY_RESOURCE_UPDATE,
        GATEWAY_STATE_CHANGED,
    )

    async def _on_catalog_signal(_event: object) -> None:
        await emitter.reconcile_from_proxy(proxy)

    proxy.event_bus.subscribe_async(GATEWAY_STATE_CHANGED, _on_catalog_signal)
    proxy.event_bus.subscribe_async(
        FEDERATION_GATEWAY_CATALOG_CHANGED, _on_catalog_signal
    )
    proxy.event_bus.subscribe_async(GATEWAY_RESOURCE_UPDATE, _on_catalog_signal)
    logger.info("Subscribed aggregate model availability reconcile handlers")


async def initialize_aggregate_model_availability(proxy: StargateProxy) -> None:
    """Create emitter, subscribe reconciles, and run an initial reconcile."""
    from systems.routing.aggregate_model_availability import (
        AggregateModelAvailabilityEmitter,
    )

    proxy.aggregate_availability_emitter = AggregateModelAvailabilityEmitter(
        proxy.event_bus
    )
    _subscribe_aggregate_model_availability(proxy)
    await proxy.aggregate_availability_emitter.reconcile_from_proxy(proxy)
    logger.info("Aggregate model availability emitter initialized")


async def initialize_intelligence_profiles(proxy: StargateProxy) -> None:
    """Initialize the intelligence profile store and derive cloud profiles.

    Creates the IntelligenceProfileStore, loads any curated YAML profiles,
    then fetches the enriched cloud catalog and derives profiles for each
    cloud model. Subscribes to catalog changes for automatic refresh.
    """
    from intelligence_profiles import IntelligenceProfileStore

    store = IntelligenceProfileStore()

    config_dir = _get_config_dir(proxy.config)
    curated_dir = config_dir / "intelligence_profiles"
    if curated_dir.is_dir():
        store.load_curated(curated_dir)

    cloud_client = _get_cloud_proxy_client(proxy)
    if cloud_client is not None:
        try:
            catalog_data = await cloud_client.get_models()
            models = catalog_data.get("models", [])
            if models:
                from systems.profiles.intelligence.deriver import derive_bulk

                profiles = derive_bulk(models)
                store.set_derived_bulk(profiles)
                logger.info(
                    "Intelligence profiles: %d derived from cloud catalog",
                    len(profiles),
                )
        except Exception as e:
            logger.exception(
                "Failed to derive intelligence profiles from cloud catalog: %s", e
            )

    proxy.intelligence_profile_store = store
    logger.info("Intelligence profile store initialized (%d profiles)", store.count)

    _subscribe_profile_refresh_on_catalog_change(proxy)


def _get_cloud_proxy_client(proxy: StargateProxy) -> object | None:
    """Extract CloudProxyClient from federation integration if available."""
    fed = getattr(proxy, "federation_integration", None)
    if fed is None:
        return None
    forwarder = getattr(fed, "forwarder", None)
    if forwarder is None:
        return None
    client = getattr(forwarder, "cloud_forwarder", None)
    if client is None or not hasattr(client, "get_models"):
        return None
    return client


def _subscribe_profile_refresh_on_catalog_change(proxy: StargateProxy) -> None:
    """Refresh derived profiles when the cloud proxy catalog changes."""
    if proxy.event_bus is None:
        return

    from src.scheduling.events import CLOUD_PROXY_CATALOG_UPDATED

    async def on_catalog_updated(event) -> None:
        store = proxy.intelligence_profile_store
        if store is None:
            return
        client = _get_cloud_proxy_client(proxy)
        if client is None:
            return
        try:
            catalog_data = await client.get_models()
            models = catalog_data.get("models", [])
            if models:
                from systems.profiles.intelligence.deriver import derive_bulk

                profiles = derive_bulk(models)
                store.set_derived_bulk(profiles)
                logger.info(
                    "Intelligence profiles refreshed: %d from catalog update",
                    len(profiles),
                )
        except Exception as e:
            logger.exception("Failed to refresh intelligence profiles: %s", e)

    proxy.event_bus.subscribe_async(CLOUD_PROXY_CATALOG_UPDATED, on_catalog_updated)
    logger.info("Subscribed to catalog updates for intelligence profile refresh")


async def initialize_pipeline_system(proxy: StargateProxy) -> None:
    """Initialize the pipeline registry/executor if available."""
    proxy.pipeline_catalog_synced = False
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
            is_model_available=create_model_checker(proxy),
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
                _old, _new = proxy.pipeline_registry.reload_pipelines()
                proxy.pipeline_catalog_synced = True
                logger.info(
                    f"🔄 Pipelines reloaded after initialization: {_old} → {_new} "
                    "(local gateway already connected)"
                )
                await _emit_pipeline_unavailable_events(proxy)
            _subscribe_pipeline_reload_on_gateway_connected(proxy)
        else:
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
                on_reload_success=lambda _old, _new: setattr(
                    proxy, "pipeline_catalog_synced", True
                ),
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
        logger.warning("Pipeline system not available: %s", exc, exc_info=True)
        proxy.pipeline_registry = None
        proxy.pipeline_executor = None
        proxy.pipeline_hot_reload = None
        proxy.pipeline_catalog_synced = False
