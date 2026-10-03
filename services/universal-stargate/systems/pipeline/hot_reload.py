"""
Pipeline configuration hot-reload.

Watches pipeline search paths and triggers reload when YAML files change.
Uses universal_hot_reload.HotReloadWatcher for pure async file monitoring.
"""

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from universal_hot_reload import HotReloadWatcher
from universal_logging import get_logger

if TYPE_CHECKING:
    from .registry import PipelineRegistry

logger = get_logger(__name__)


class PipelineHotReload:
    """
    Manages hot-reload for pipeline configurations.

    Watches all search paths from PipelineRegistry and triggers
    reload_pipelines() when YAML files change.
    """

    def __init__(
        self,
        registry: "PipelineRegistry",
        debounce_ms: int = 2000,
        enabled: bool = True,
        on_reload_success: Callable[[int, int], None] | None = None,
    ):
        """
        Initialize pipeline hot-reload.

        Args:
            registry: PipelineRegistry to reload
            debounce_ms: Debounce delay (2s default for pipeline stability)
            enabled: Whether hot-reload is enabled
            on_reload_success: Optional callback after successful reload
        """
        self.registry = registry
        self.debounce_ms = debounce_ms
        self.enabled = enabled
        self.on_reload_success = on_reload_success
        self._watchers: list[HotReloadWatcher] = []

    async def start(self) -> bool:
        """Start watching all pipeline search paths."""
        if not self.enabled:
            logger.info("Pipeline hot-reload disabled")
            return False

        try:
            paths_watched = 0
            for search_path in self.registry._search_paths:
                # Use same path resolution logic as PipelineRegistry
                expanded = Path(search_path).expanduser()

                # Resolve relative paths relative to config_base_dir
                # Absolute paths and paths starting with ~ are left as-is
                if not expanded.is_absolute():
                    path = (self.registry._config_base_dir / expanded).resolve()
                else:
                    path = expanded.resolve()

                if not path.exists():
                    logger.debug(f"Pipeline path does not exist: {path}")
                    continue

                watcher = HotReloadWatcher(
                    name=f"pipeline:{path.name}",
                    watch_path=path,
                    on_change=self._reload_callback,
                    debounce_ms=self.debounce_ms,
                    recursive=True,  # Watch domain subdirectories
                    patterns=[".yaml", ".yml"],
                )

                if await watcher.start():
                    self._watchers.append(watcher)
                    paths_watched += 1

            if paths_watched == 0:
                logger.warning("No pipeline paths available to watch")
                return False

            logger.info(
                f"🔥 Pipeline hot-reload active: "
                f"{paths_watched} path(s), debounce={self.debounce_ms}ms"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to start pipeline hot-reload: {e}", exc_info=True)
            return False

    async def stop(self):
        """Stop all watchers."""
        for watcher in self._watchers:
            await watcher.stop()
        self._watchers.clear()
        logger.info("🛑 Pipeline hot-reload stopped")

    async def _reload_callback(self, file_path: str):
        """Callback when pipeline config changes.

        Args:
            file_path: Path to the changed file (from HotReloadWatcher)
        """
        try:
            # reload_pipelines() returns (old_count, new_count) to avoid race condition
            old_count, new_count = self.registry.reload_pipelines()
            if self.on_reload_success is not None:
                self.on_reload_success(old_count, new_count)

            logger.info(
                f"🔄 Pipeline reload ({Path(file_path).name}): "
                f"{old_count} → {new_count} pipelines"
            )
        except Exception as e:
            logger.error(f"Pipeline reload failed: {e}", exc_info=True)
            raise

    def get_status(self) -> dict:
        """Get hot-reload status."""
        return {
            "enabled": self.enabled,
            "watchers": [w.get_status() for w in self._watchers],
            "debounce_ms": self.debounce_ms,
        }
