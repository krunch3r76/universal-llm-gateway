"""
Gateway-specific hot reload manager.

Uses universal_hot_reload.HotReloadWatcher for file monitoring,
adds Gateway-specific features: ConfigReloader, rollback, metrics.
"""
import asyncio
from pathlib import Path

from universal_hot_reload import HotReloadWatcher
from universal_logging import get_logger

try:
    from ..hot_reload_metrics import hot_reload_metrics
except ImportError:
    from src.core.hot_reload_metrics import hot_reload_metrics

from .reload import ConfigReloader
from .types import HotReloadStatus, ReloadEvent

logger = get_logger(__name__)


class HotReloadManager:
    """
    Gateway hot reload manager.
    
    Wraps shared HotReloadWatcher with Gateway-specific functionality:
    - ConfigReloader for parsing, validation, merge
    - Automatic rollback on failure
    - Metrics collection
    - Security validation (allowed_paths, max_file_size)
    - Per-file reload callbacks
    
    Architecture:
        HotReloadWatcher (shared lib) → file change detected
        → HotReloadManager._on_file_change(file_path) → per-file queue
        → ConfigReloader.execute_reload() → validation, merge, rollback
    """

    def __init__(
        self,
        config_manager,  # Unused, kept for API compatibility
        model_registry,
        watch_directory: str | Path = "config",
        debounce_ms: int = 500,
        recursive: bool = True,
        supported_formats: list[str] | None = None,
        allowed_paths: list[str] | None = None,
        max_file_size_mb: int = 10,
        log_level: str = "info",  # Unused, kept for API compatibility
    ):
        """Initialize hot reload manager.
        
        Args:
            config_manager: Configuration manager instance (unused, kept for API)
            model_registry: Model registry instance for config updates
            watch_directory: Directory to watch for changes
            debounce_ms: Debounce delay in milliseconds
            recursive: Whether to watch subdirectories
            supported_formats: List of supported file extensions
            allowed_paths: List of allowed path prefixes for security
            max_file_size_mb: Maximum file size to process
            log_level: Logging level (unused, kept for API)
        """
        self.model_registry = model_registry
        self.watch_directory = Path(watch_directory)
        self.debounce_ms = debounce_ms
        self.recursive = recursive
        self.supported_formats = supported_formats or [".yaml", ".yml", ".json"]
        self.allowed_paths = allowed_paths or ["config"]
        self.max_file_size_mb = max_file_size_mb

        # State tracking
        self.enabled = False
        self.recent_changes: list[ReloadEvent] = []
        self.max_recent_changes = 50
        self.error_count = 0

        # Callbacks for reload events
        self.on_reload_callbacks: list = []

        # Per-file reload queues for serialization (keeps order within file)
        self._reload_queues: dict[str, asyncio.Queue] = {}
        self._reload_workers: dict[str, asyncio.Task] = {}

        # Config reloader handles parsing, validation, and merge
        self._reloader = ConfigReloader(
            model_registry=model_registry,
            allowed_paths=self.allowed_paths,
            max_file_size_mb=max_file_size_mb,
        )

        # Shared watcher for file monitoring (replaces ~150 lines of duplicate code)
        self._watcher = HotReloadWatcher(
            name="gateway-config",
            watch_path=watch_directory,
            on_change=self._on_file_change,
            debounce_ms=debounce_ms,
            recursive=recursive,
            patterns=self.supported_formats,
        )

    async def start(self) -> bool:
        """Start hot reload monitoring.
        
        Returns:
            True if started successfully, False otherwise
        """
        if self.enabled:
            logger.warning("Hot reload already enabled")
            return True

        if not self.watch_directory.exists():
            logger.error(f"Watch directory does not exist: {self.watch_directory}")
            return False

        try:
            if await self._watcher.start():
                self.enabled = True
                hot_reload_metrics.update_hot_reload_status(True)
                hot_reload_metrics.update_observer_status(True)
                hot_reload_metrics.update_config_info(
                    str(self.watch_directory),
                    self.debounce_ms,
                    self.recursive,
                    self.supported_formats,
                )
                logger.info(
                    f"✅ Hot reload started - watching {self.watch_directory} "
                    f"(debounce={self.debounce_ms}ms, recursive={self.recursive})"
                )
                return True
            else:
                logger.error("Failed to start watcher")
                return False

        except Exception as e:
            logger.error(f"Failed to start hot reload: {e}")
            self.error_count += 1
            return False

    async def stop(self):
        """Stop hot reload monitoring."""
        if not self.enabled:
            return

        self.enabled = False

        # Stop the shared watcher
        await self._watcher.stop()

        # Cancel per-file workers
        for worker in self._reload_workers.values():
            _ = worker.cancel()
        self._reload_queues.clear()
        self._reload_workers.clear()

        hot_reload_metrics.update_hot_reload_status(False)
        hot_reload_metrics.update_observer_status(False)

        logger.info("Hot reload stopped")

    async def _on_file_change(self, file_path: str):
        """Called by shared watcher when a specific file changes.
        
        Args:
            file_path: Path to the changed file (from HotReloadWatcher)
        
        The shared watcher already handles debouncing and pattern filtering.
        We just need to queue the reload for this specific file.
        """
        logger.debug(f"File change detected: {file_path}")
        await self.queue_reload(file_path)

    def add_reload_callback(self, callback):
        """Add a callback to be called after successful reload."""
        self.on_reload_callbacks.append(callback)

    async def queue_reload(self, file_path: str):
        """Queue a file for reload (for manual triggers or per-file handling)."""
        if file_path not in self._reload_queues:
            self._reload_queues[file_path] = asyncio.Queue()
            self._reload_workers[file_path] = asyncio.create_task(
                self._reload_worker(file_path)
            )
        await self._reload_queues[file_path].put(True)

    async def _reload_worker(self, file_path: str):
        """Worker that processes reload requests for a specific file."""
        queue = self._reload_queues[file_path]
        try:
            while True:
                await queue.get()

                # Drain queue (coalesce rapid changes)
                while not queue.empty():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                event = await self._reloader.execute_reload(file_path)
                self._record_change(event)

                if event.success:
                    for callback in self.on_reload_callbacks:
                        try:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(event)
                            else:
                                callback(event)
                        except Exception as e:
                            logger.error(f"Reload callback error: {e}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Reload worker error for {file_path}: {e}")
            self.error_count += 1

    def _record_change(self, event: ReloadEvent):
        """Record a reload event in recent changes."""
        self.recent_changes.append(event)
        if len(self.recent_changes) > self.max_recent_changes:
            self.recent_changes.pop(0)

    async def reload_config_file(self, file_path: str) -> ReloadEvent:
        """Manually reload a specific configuration file."""
        path = Path(file_path)
        self._validate_watch_directory(path)
        self._validate_file_size(path)
        return await self._reloader.execute_reload(file_path)

    def _validate_watch_directory(self, path: Path):
        """Validate file is in allowed watch directory."""
        try:
            _ = path.resolve().relative_to(self.watch_directory.resolve())
        except ValueError:
            raise ValueError(f"File {path} is not in watch directory")

    def _validate_file_size(self, path: Path):
        """Validate file size is within limits."""
        if path.exists():
            size_mb = path.stat().st_size / (1024 * 1024)
            if size_mb > self.max_file_size_mb:
                raise ValueError(f"File {path} exceeds max size ({size_mb:.1f}MB > {self.max_file_size_mb}MB)")

    def get_status(self) -> HotReloadStatus:
        """Get current hot reload status."""
        watcher_status = self._watcher.get_status()
        return HotReloadStatus(
            enabled=self.enabled,
            watch_directory=str(self.watch_directory),
            last_reload=(
                self.recent_changes[-1].timestamp if self.recent_changes else None
            ),
            recent_changes=self.recent_changes[-10:],
            error_count=self.error_count + watcher_status["error_count"],
        )
