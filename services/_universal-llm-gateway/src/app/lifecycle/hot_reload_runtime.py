"""Hot reload lifecycle management.

Owns creating/configuring HotReloadManager, emitting CATALOG_RELOADED after
successful reloads via the private _emit_catalog_reloaded callback, starting
monitoring, and storing the manager on app.state. The original nested closure
has been lifted to a module-private async function plus a minimal local
wrapper closure at registration time (required because event_bus is
per-invocation).
"""

from ...core.config_manager import ConfigManager
from ...core.hot_reload import HotReloadManager
from .logging_bootstrap import get_gateway_logger


async def _emit_catalog_reloaded(reload_event, *, event_bus) -> None:
    """Module-private callback that emits CATALOG_RELOADED on successful hot reload.

    Registered (via a thin local wrapper) with HotReloadManager so that
    configuration changes automatically notify the rest of the system.
    The original nested `emit_catalog_reloaded` inside lifespan has been
    replaced by this reusable private function.
    """
    from ...core.events.types import CatalogReloaded

    if reload_event.success:
        catalog_event = CatalogReloaded(
            reason=f"hot_reload:{reload_event.file_path}",
        )
        await event_bus.publish_nowait(catalog_event)
        gateway_logger = get_gateway_logger()
        if gateway_logger is not None:
            gateway_logger.info(
                f"Emitted CATALOG_RELOADED for {reload_event.model_key}"
            )


async def start_hot_reload_manager(
    app, gateway_config, model_registry, event_bus
) -> None:
    """Initialize and start the HotReloadManager when enabled in gateway_config.

    If hot reload is disabled, simply logs and returns without touching state.
    On any initialization or start failure, the manager is left as None and
    an error is logged; the gateway continues without hot reload.
    """
    gateway_logger = get_gateway_logger()

    hot_reload_manager = None
    if gateway_config.hot_reload.enabled:
        try:
            # Create configuration manager for hot reload
            config_manager = ConfigManager(gateway_config.model_registry.config_file)

            # Initialize hot reload manager
            hot_reload_manager = HotReloadManager(
                config_manager=config_manager,
                model_registry=model_registry,
                watch_directory=gateway_config.hot_reload.watch_directory,
                debounce_ms=gateway_config.hot_reload.debounce_ms,
                recursive=gateway_config.hot_reload.recursive,
                supported_formats=gateway_config.hot_reload.supported_formats,
                log_level=gateway_config.hot_reload.log_level,
            )

            # Register callback using a local wrapper closure that binds event_bus
            # and delegates to the module-level private _emit_catalog_reloaded.
            # This preserves the original behavior while eliminating the nested
            # async def that lived inside the lifespan function.
            async def _emit_for_this_manager(reload_event):
                await _emit_catalog_reloaded(reload_event, event_bus=event_bus)

            hot_reload_manager.add_reload_callback(_emit_for_this_manager)

            # Start hot reload monitoring
            if await hot_reload_manager.start():
                app.state.hot_reload_manager = hot_reload_manager
                if gateway_logger is not None:
                    gateway_logger.info(
                        f"Hot reload monitoring started: "
                        f"watch_directory={gateway_config.hot_reload.watch_directory}, "
                        f"debounce_ms={gateway_config.hot_reload.debounce_ms}, "
                        f"recursive={gateway_config.hot_reload.recursive}"
                    )
            else:
                if gateway_logger is not None:
                    gateway_logger.error("Failed to start hot reload monitoring")
                hot_reload_manager = None

        except Exception as e:
            if gateway_logger is not None:
                gateway_logger.error(
                    f"Failed to initialize hot reload manager: {e}", exc_info=True
                )
            hot_reload_manager = None
    else:
        if gateway_logger is not None:
            gateway_logger.info("Hot reload disabled in configuration")
