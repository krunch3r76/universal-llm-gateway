"""Worker controller lifecycle and idle-notification mixin."""

from collections.abc import Awaitable, Callable

from ._runtime import _get_resource_tracker, logger


class LifecycleMixin:
    """Start/stop/shutdown and idle-callback orchestration."""

    async def start(self):
        logger.info("🚀 Starting WorkerController...")
        # Subscribe to INFERENCE_COMPLETED to check for idle state (graceful shutdown)
        if self.event_bus:
            try:
                from src.core.events.types import INFERENCE_COMPLETED

                self.event_bus.subscribe_async(
                    INFERENCE_COMPLETED, self._on_inference_completed
                )
            except Exception as e:
                logger.warning(f"Could not subscribe to INFERENCE_COMPLETED: {e}")

    async def _on_inference_completed(self, event) -> None:
        """Handle INFERENCE_COMPLETED event to check for idle state."""
        await self._notify_idle_if_needed()

    async def stop(self):
        logger.info("🛑 Stopping WorkerController...")
        await self.shutdown()

    async def shutdown(self):
        logger.info("🛑 Shutting down WorkerController...")
        if await self._lifecycle_manager.shutdown():
            for mid in list(_get_resource_tracker().get_all_models_info().keys()):
                _get_resource_tracker().unregister_model(mid)
            return True
        return False

    def is_idle(self) -> bool:
        """Check if controller has no in-flight work. Used for graceful shutdown."""
        try:
            return len(_get_resource_tracker().get_busy_models()) == 0
        except Exception:
            return False

    def register_idle_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Register callback to be called when controller becomes idle.

        Used for graceful shutdown to notify when all work is complete.
        """
        self._idle_callbacks.append(callback)

    async def _notify_idle_if_needed(self) -> None:
        """Notify idle callbacks if controller is now idle."""
        if not self._idle_callbacks:
            return
        if not self.is_idle():
            return
        for callback in self._idle_callbacks:
            try:
                await callback()
            except Exception as e:
                logger.error(f"Idle callback error: {e}")
