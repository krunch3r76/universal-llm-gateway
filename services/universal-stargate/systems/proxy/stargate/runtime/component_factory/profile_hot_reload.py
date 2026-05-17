"""Profile hot-reload initialization for StargateProxy.

This module owns the wiring of a HotReloadWatcher onto the already-initialized
ProfileManager. It is deliberately separate from profile manager construction
so that the heavy I/O of loading profiles can be completed before any watcher
or event-loop concerns are introduced.

The watcher runs reload_profiles in a thread to avoid blocking the event loop
on synchronous YAML parsing.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ...proxy import StargateProxy

logger = get_logger(__name__)


async def initialize_hot_reload(proxy: StargateProxy) -> None:
    """Initialize hot-reload watchers for profiles.

    Attaches a HotReloadWatcher (from universal_hot_reload) to the public
    proxy.profile_manager.profiles_path. The watcher is stored as
    proxy.profile_watcher for later shutdown.

    The on_change callback is executed via asyncio.to_thread so that the
    synchronous ProfileManager.reload_profiles() call cannot block the
    event loop.

    Safe to call even when profile_manager is None (watcher is set to None
    and a warning is logged).

    Args:
        proxy: StargateProxy whose profile_manager (if present) will be watched.
    """
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
