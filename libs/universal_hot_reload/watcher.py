"""
Generic async file watcher with debouncing.

Pure async implementation using watchfiles (Rust's notify crate).
No threading, no locks - all operations in async context.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from universal_logging import get_logger
from watchfiles import Change, awatch

from .path_filters import matches_watch_exclude

logger = get_logger(__name__)


class HotReloadWatcher:
    """
    Async file watcher with debouncing.

    Pure async implementation - no threads, no locks.
    Uses watchfiles (Rust's notify crate) for efficient file monitoring.

    Features:
    - Watch single file or directory (recursive)
    - Async debouncing via asyncio.sleep()
    - Configurable file patterns
    - Graceful shutdown
    - Callback receives file path for per-file handling
    """

    def __init__(
        self,
        name: str,
        watch_path: str | Path,
        on_change: Callable[[str], Awaitable[None]],
        debounce_ms: int = 1000,
        recursive: bool = False,
        patterns: list[str] | None = None,
        exclude: list[str] | None = None,
        on_delete: Callable[[str], Awaitable[None]] | None = None,
    ):
        """
        Initialize hot-reload watcher.

        Args:
            name: Watcher name for logging
            watch_path: Path to watch (file or directory)
            on_change: Async callback when change detected (receives file_path)
            debounce_ms: Milliseconds to wait before triggering callback
            recursive: Watch subdirectories (for directories only)
            patterns: File patterns to watch (e.g., [".yaml", ".yml", "yaml"])
                      Patterns are normalized to include leading dot.
            exclude: fnmatch globs matched against the watch-root-relative path
                     (e.g. ["trading/**"]) and bare filename globs matched
                     against basenames (e.g. ["CORPUS_MANIFEST.md"]).
            on_delete: Optional async callback for file deletion events.
        """
        self.name = name
        self.watch_path = Path(watch_path).expanduser().resolve()
        self.on_change = on_change
        self.on_delete = on_delete
        self.debounce_ms = debounce_ms
        self.recursive = recursive
        # Normalize patterns: ensure leading dot (handle ".yaml" or "yaml")
        self.patterns = {f".{p.lstrip('.')}" for p in (patterns or [".yaml", ".yml"])}
        self.exclude = exclude or []

        self.watch_task: asyncio.Task[None] | None = None
        self.debounce_task: asyncio.Task[None] | None = None
        self.reload_count = 0
        self.error_count = 0
        self._enabled = False
        self._pending_file: str | None = None
        self._pending_callback: Callable[[str], Awaitable[None]] | None = None

    async def start(self) -> bool:
        """
        Start watching for file changes.

        Returns:
            True if watching started successfully
        """
        if self._enabled:
            logger.warning(f"[{self.name}] Already watching")
            return True

        if not self.watch_path.exists():
            logger.warning(f"[{self.name}] Path does not exist: {self.watch_path}")
            return False

        try:
            self._enabled = True
            self.watch_task = asyncio.create_task(
                self._watch_loop(), name=f"HotReload-{self.name}"
            )

            watch_type = "directory (recursive)" if self.recursive else "path"
            logger.info(
                f"🔍 [{self.name}] Hot-reload started - "
                f"watching {watch_type}: {self.watch_path}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self.name}] Failed to start: {e}", exc_info=True)
            self._enabled = False
            return False

    async def stop(self):
        """Stop watching and cleanup."""
        if not self._enabled:
            return

        self._enabled = False

        if self.debounce_task:
            _ = self.debounce_task.cancel()
            try:
                await self.debounce_task
            except asyncio.CancelledError:
                pass
            self.debounce_task = None

        if self.watch_task:
            _ = self.watch_task.cancel()
            try:
                await self.watch_task
            except asyncio.CancelledError:
                pass
            self.watch_task = None

        logger.info(
            f"🛑 [{self.name}] Hot-reload stopped "
            f"(reloads={self.reload_count}, errors={self.error_count})"
        )

    async def _watch_loop(self):
        """Watch for file changes (pure async)."""
        logger.info(f"🔍 [{self.name}] _watch_loop STARTED")

        try:
            async for changes in awatch(
                self.watch_path,
                recursive=self.recursive,
                step=100,  # Check every 100ms
            ):
                if not self._enabled:
                    logger.info(f"🔍 [{self.name}] _enabled=False, breaking")
                    break

                for change_type, file_path in changes:
                    await self._handle_change(change_type, file_path)

            raise RuntimeError(
                f"[{self.name}] awatch() completed unexpectedly for {self.watch_path}"
            )

        except asyncio.CancelledError:
            logger.info(
                f"🔍 [{self.name}] Watch loop cancelled (expected during shutdown)"
            )
            raise
        except Exception as e:
            logger.error(f"🚨 [{self.name}] Watch loop CRASHED: {e}", exc_info=True)
            logger.error(
                f"🚨 Watch path: {self.watch_path}, exists: {self.watch_path.exists()}"
            )
            self.error_count += 1
            # DO NOT SILENTLY EXIT - log prominently and re-raise
            raise  # Re-raise to make task exception visible
        finally:
            if self._enabled:
                logger.warning(f"🔍 [{self.name}] _watch_loop EXITING unexpectedly")
            else:
                logger.info(f"🔍 [{self.name}] _watch_loop EXITING gracefully")

    async def _handle_change(self, change_type: Change, file_path: str) -> None:
        """Handle file change with debouncing."""
        path = Path(file_path)

        # Check if file matches patterns
        if not any(path.suffix == pat for pat in self.patterns):
            return

        # Skip temp/backup files
        if path.name.startswith(".") or path.name.endswith(
            ("~", ".bak", ".swp", ".tmp")
        ):
            return

        if matches_watch_exclude(
            path, watch_root=self.watch_path, patterns=self.exclude
        ):
            return

        if change_type == Change.deleted:
            if self.on_delete is None:
                return
            callback: Callable[[str], Awaitable[None]] = self.on_delete
        else:
            callback = self.on_change

        logger.debug(f"[{self.name}] {change_type.name}: {file_path}")

        # Cancel existing debounce
        if self.debounce_task:
            _ = self.debounce_task.cancel()
            try:
                await self.debounce_task
            except asyncio.CancelledError:
                pass

        # Track the file that triggered the change
        self._pending_file = file_path
        self._pending_callback = callback

        # Start new debounce
        self.debounce_task = asyncio.create_task(
            self._debounced_callback(file_path, callback)
        )

    async def _debounced_callback(
        self,
        file_path: str,
        callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """Execute callback after debounce delay."""
        try:
            await asyncio.sleep(self.debounce_ms / 1000.0)
            self.debounce_task = None
            self._pending_file = None
            self._pending_callback = None

            logger.info(f"🔄 [{self.name}] Config changed: {file_path}")

            try:
                await callback(file_path)
                self.reload_count += 1
                logger.info(f"✅ [{self.name}] Hot-reload complete")
            except Exception as e:
                self.error_count += 1
                logger.error(f"❌ [{self.name}] Hot-reload failed: {e}", exc_info=True)

        except asyncio.CancelledError:
            pass

    def get_status(self) -> dict[str, object]:
        """Get watcher status."""
        return {
            "name": self.name,
            "enabled": self._enabled,
            "watching": self.watch_task is not None and not self.watch_task.done(),
            "path": str(self.watch_path),
            "recursive": self.recursive,
            "patterns": sorted(self.patterns),
            "debounce_ms": self.debounce_ms,
            "reload_count": self.reload_count,
            "error_count": self.error_count,
        }
