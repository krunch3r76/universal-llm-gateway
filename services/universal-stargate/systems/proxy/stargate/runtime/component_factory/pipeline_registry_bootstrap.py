"""Pipeline registry, executor, and hot-reload bootstrap for Stargate.

This module owns the entire pipeline subsystem initialization:

- Construction of PipelineRegistry with a live model-availability checker
- Loading of user domain handlers from configured search paths
- Creation of PipelineExecutor wired to the request executor
- Async dispatch tracker (PipelineExecutionTracker) for /pipelines/dispatch
- Optional PipelineHotReload watcher
- Subscription to GATEWAY_STATE_CHANGED, FEDERATION_GATEWAY_CATALOG_CHANGED,
  and FEDERATION_GATEWAY_REACHABILITY_RESTORED so pipelines re-gate when
  catalogs change or a federated gateway returns from UNREACHABLE
- Emission of pipeline.registry.unavailable events for permanently-skipped
  pipelines (so operators can observe missing model dependencies)

All of the above is kept in one module because the reload callbacks,
unavailable-event emission, and catalog-subscription logic are tightly
coupled to the registry lifecycle. The module has no intra-package imports.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

from systems.routing.selection.catalog import get_all_available_models

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...proxy import StargateProxy

logger = get_logger(__name__)


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
            await proxy.event_bus.publish_nowait(event)
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


async def _reload_pipelines_after_federation_event(
    proxy: StargateProxy, event: object, *, reason: str
) -> None:
    """Re-gate pipeline registry against the live federated model union."""
    if proxy.pipeline_registry is None:
        return

    payload = getattr(event, "payload", None) or {}
    gateway_id = payload.get("gateway_id", "unknown")
    try:
        old_count, new_count = await asyncio.to_thread(
            proxy.pipeline_registry.reload_pipelines
        )
        proxy.pipeline_catalog_synced = True
        logger.info(
            "🔄 Pipelines re-gated after %s from %s: %d → %d pipelines",
            reason,
            gateway_id,
            old_count,
            new_count,
        )
        await _emit_pipeline_unavailable_events(proxy)
    except Exception:
        logger.exception("Pipeline reload failed after %s from %s", reason, gateway_id)


def _subscribe_pipeline_reload_on_federation_signals(proxy: StargateProxy) -> None:
    """
    Subscribe to federation catalog and reachability signals for pipeline reload.

    Catalog-changed covers new/removed model IDs. Reachability-restored covers
    UNREACHABLE→REACHABLE when the cached catalog set is unchanged (e.g. relay
    reconnect after outage) so dependency gating still re-runs.

    INVARIANT: reload_pipelines() runs off-thread to avoid blocking event loop.
    """
    if proxy.federation_integration is None or proxy.event_bus is None:
        return

    from src.scheduling.events import (
        FEDERATION_GATEWAY_CATALOG_CHANGED,
        FEDERATION_GATEWAY_REACHABILITY_RESTORED,
    )

    async def on_catalog_changed(event: object) -> None:
        await _reload_pipelines_after_federation_event(
            proxy, event, reason="catalog change"
        )

    async def on_reachability_restored(event: object) -> None:
        await _reload_pipelines_after_federation_event(
            proxy, event, reason="reachability restored"
        )

    proxy.event_bus.subscribe_async(
        FEDERATION_GATEWAY_CATALOG_CHANGED, on_catalog_changed
    )
    proxy.event_bus.subscribe_async(
        FEDERATION_GATEWAY_REACHABILITY_RESTORED, on_reachability_restored
    )
    logger.info(
        "📦 Subscribed to federation catalog + reachability signals for pipeline reload"
    )


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

        if proxy.federation_integration is not None:
            _subscribe_pipeline_reload_on_federation_signals(proxy)
            # Catch up on catalog snapshots that arrived before subscribers
            # were wired (startup race: federation fires catalog events during
            # federated_manager setup, which precedes pipeline system init).
            if (
                proxy.federated_manager is not None
                and proxy.federated_manager.has_any_catalog_data()
            ):
                _old, _new = proxy.pipeline_registry.reload_pipelines()
                proxy.pipeline_catalog_synced = True
                logger.info(
                    "🔄 Pipelines reloaded on startup (federation catalog "
                    "pre-populated): %d → %d",
                    _old,
                    _new,
                )
                await _emit_pipeline_unavailable_events(proxy)

        proxy.pipeline_executor = PipelineExecutor(
            registry=proxy.pipeline_registry,
            request_executor=proxy.request_executor,
            proxy=proxy,
        )

        # Async dispatch tracker (phase 1: in-process, TTL-pruned records).
        # Shared by POST /api/v1/pipelines/dispatch + GET .../executions/{id}.
        from functools import partial

        from systems.pipeline.core.execution.async_tracker import (
            PipelineExecutionTracker,
        )
        from systems.pipeline.core.execution.async_tracker_delivery import (
            deliver_result,
        )

        _agent_bus_token = os.environ.get("AGENT_BUS_TOKEN", "")
        if not _agent_bus_token:
            logger.warning(
                "AGENT_BUS_TOKEN not set; async-dispatch result delivery to "
                "agent-bus will be disabled. Set AGENT_BUS_TOKEN in the "
                "Stargate env to enable."
            )
        _delivery_sender = (
            partial(
                deliver_result,
                event_bus=proxy.event_bus,
                auth_token=_agent_bus_token,
            )
            if _agent_bus_token
            else None
        )

        proxy.pipeline_dispatch_tracker = PipelineExecutionTracker(
            event_bus=proxy.event_bus,
            delivery_sender=_delivery_sender,
            agent_bus_token=_agent_bus_token,
        )
        logger.info("✅ PipelineExecutionTracker initialized for async dispatch")

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
