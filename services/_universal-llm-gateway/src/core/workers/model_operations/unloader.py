"""Model unloading operations for WorkerController."""

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from universal_logging import get_logger

from ..utils import is_model_active

if TYPE_CHECKING:
    from ..controller import WorkerController

__all__ = ["ModelUnloader", "UnloadResult"]


async def _publish_socket_cleanup(model_id: str) -> None:
    """
    Publish socket cleanup event for a model (non-blocking).

    Fire-and-forget event publication. Does not wait for cleanup completion.

    Invariant: non_blocking ∧ event_published

    Args:
        model_id: Model identifier for socket cleanup

    Side Effects:
        Publishes SocketCleanupRequested event to event bus

    Note:
        Uses publish_async_nowait() to avoid blocking caller.
        Event handler executes asynchronously.
    """
    from ...events import get_event_bus
    from ..process.communication import SocketCleanupRequested
    from ..utils import get_universal_protocol_socket_path

    socket_path = get_universal_protocol_socket_path(model_id)
    await get_event_bus().publish_async_nowait(
        SocketCleanupRequested(model_id=model_id, socket_path=socket_path)
    )


@dataclass
class UnloadResult:
    """
    Result of model unload operation.

    Attributes:
        success: True if model was unloaded successfully
        skipped: True if unload was skipped (e.g., model busy)
        reason: Detailed reason code (e.g., "unloaded", "model_busy", "unload_failed")
    """

    success: bool
    skipped: bool
    reason: str


def _get_resource_tracker():
    """Lazy import to avoid circular dependency."""
    from src.core.resources import resource_tracker

    return resource_tracker


def _get_event_classes():
    """Lazy import of event classes."""
    from src.core.events.types import ModelUnloaded, ModelUnloadingStarted

    return ModelUnloaded, ModelUnloadingStarted


async def _publish_event(event_bus, event) -> bool:
    """Publish event with error handling. Returns True if published."""
    if not event_bus:
        return False
    try:
        await event_bus.publish_async_nowait(event)
        return True
    except Exception as e:
        get_logger(__name__).warning(f"⚠️ Failed to publish event: {e}")
        return False


logger = get_logger(__name__)
structured_logger = get_logger("universal_llm_gateway.model_unloader")


class ModelUnloader:
    """
    Handles model unloading operations.

    Extracted from WorkerController to reduce file size.
    """

    def __init__(self, controller: "WorkerController"):
        self._controller = controller

    async def unload_current_model(self) -> UnloadResult:
        """Unload the currently active model."""
        active_model = self._controller.get_active_model_id()
        if not active_model:
            logger.info("ℹ️ No active model to unload")
            return UnloadResult(success=True, skipped=True, reason="no_active_model")
        return await self.unload_model(active_model)

    async def unload_model(self, model_id: str, force: bool = False) -> UnloadResult:
        """
        Unload a model with proper cleanup.

        Args:
            model_id: Model to unload
            force: If True, kill process immediately bypassing busy check

        Returns:
            UnloadResult with success status, skip flag, and reason
        """
        if not model_id:
            logger.warning("⚠️ Cannot unload model: model_id is None")
            return UnloadResult(success=False, skipped=False, reason="no_model_id")

        logger.info(f"🛑 Unloading model: {model_id} (force={force})")

        try:
            resource_tracker = _get_resource_tracker()
            current_info = resource_tracker.get_model_info(model_id)

            # Only skip for active models if NOT forcing
            if not force and current_info and is_model_active(current_info.status):
                logger.warning(
                    f"⏳ Skipping unload for active model {model_id} "
                    f"(state={current_info.status.value})"
                )
                return UnloadResult(
                    success=False,
                    skipped=True,
                    reason=f"model_{current_info.status.value}",
                )

            # Gate: check if *any* variant on the shared process is busy
            if not force and resource_tracker.is_process_in_use(model_id):
                busy = resource_tracker.describe_busy_variants(model_id)
                logger.warning(
                    "⏳ Skipping unload for %s — sibling variants busy: %s",
                    model_id,
                    busy,
                )
                return UnloadResult(
                    success=False,
                    skipped=True,
                    reason=f"sibling_busy:{','.join(busy)}",
                )

            if force and current_info and is_model_active(current_info.status):
                logger.warning(
                    f"⚡ FORCE unloading active model {model_id} "
                    f"(state={current_info.status.value})"
                )

            model_unloaded, model_unloading_started = _get_event_classes()

            await _publish_event(
                self._controller.event_bus,
                model_unloading_started(model_id=model_id),
            )

            resource_tracker.set_model_unloading(model_id)

            structured_logger.info(
                f"{model_id}:worker_shutting_down: {model_id} - SUCCESS"
            )

            supervisor = self._controller._process_state.get_supervisor(model_id)
            if not supervisor:
                logger.warning(
                    f"⚠️ No supervisor found for {model_id}, may already be unloaded"
                )
                self._mark_unloaded(model_id)
                return UnloadResult(
                    success=True, skipped=True, reason="already_unloaded"
                )

            # Force always uses fast unload (immediate kill)
            fast_unload = force or getattr(
                self._controller.gateway_config.process_isolation,
                "fast_model_unload",
                True,
            )

            success = await self._perform_unload(model_id, supervisor, fast_unload)

            if success:
                self._mark_unloaded(model_id)
                await _publish_event(
                    self._controller.event_bus, model_unloaded(model_id=model_id)
                )
                # Publish updated resource info after model unload
                await self._publish_resource_update()
                logger.info(f"✅ Model {model_id} unloaded successfully")
                return UnloadResult(success=True, skipped=False, reason="unloaded")
            else:
                terminal_reason = await self._finalize_failed_unload(
                    model_id, force=force
                )
                logger.error(
                    "❌ Failed to unload model %s (terminal_state=%s)",
                    model_id,
                    terminal_reason,
                )
                return UnloadResult(
                    success=False,
                    skipped=False,
                    reason=f"unload_failed:{terminal_reason}",
                )

        except Exception as e:
            terminal_reason = await self._finalize_failed_unload(model_id, force=force)
            logger.error(f"❌ Error unloading model {model_id}: {e}")
            return UnloadResult(
                success=False,
                skipped=False,
                reason=f"exception:{str(e)[:50]}:{terminal_reason}",
            )

    async def _perform_unload(
        self, model_id: str, supervisor, fast_unload: bool
    ) -> bool:
        """Perform the actual unload operation."""
        success = False

        try:
            if fast_unload:
                success = await self._fast_unload(model_id, supervisor)
            else:
                success = await self._standard_unload(model_id, supervisor)
        except Exception as e:
            logger.error(f"❌ Error unloading model {model_id}: {e}")
            success = False

        return success

    def _get_tracked_worker_pid(self, supervisor) -> int | None:
        """Return the currently tracked worker PID, if any."""
        try:
            info = supervisor.get_worker_info()
        except Exception as e:
            logger.warning(f"⚠️ Failed to get worker info during unload: {e}")
            return None

        pid = getattr(info, "pid", None)
        return pid if isinstance(pid, int) else None

    def _is_pid_gone(self, pid: int | None) -> bool:
        """Return True iff the tracked worker PID no longer exists."""
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return False
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        except OSError:
            return True

    async def _fast_unload(self, model_id: str, supervisor) -> bool:
        """
        Perform fast model unload using event-driven socket cleanup.

        Forcefully terminates worker process and publishes SocketCleanupRequested
        event (non-blocking) for background socket cleanup. Used when quick
        unload is needed.

        Invariant: event_published ∧ non_blocking ∧ process_terminated

        Args:
            model_id: Model identifier to unload
            supervisor: Process supervisor for the worker

        Returns:
            True if unload successful, False otherwise

        Side Effects:
            - Terminates worker process forcefully (timeout=5s)
            - Publishes SocketCleanupRequested event (fire-and-forget)
            - Logs unload progress

        Note:
            This method does NOT wait for socket cleanup completion.
            Event handler executes asynchronously in background.
        """
        logger.info(f"🚀 Fast model unload for {model_id}")

        try:
            tracked_pid = self._get_tracked_worker_pid(supervisor)
            stopped = await supervisor.stop(force=True, timeout=5)
            if not stopped:
                logger.error(
                    f"❌ Process termination returned unsuccessful for {model_id} "
                    f"(tracked_pid={tracked_pid})"
                )
                return False
            if not self._is_pid_gone(tracked_pid):
                logger.error(
                    f"❌ Tracked worker PID still alive after unload for {model_id} "
                    f"(tracked_pid={tracked_pid})"
                )
                return False

            logger.info(
                f"✅ Process termination confirmed for {model_id} "
                f"(tracked_pid={tracked_pid})"
            )
            self._remove_supervisor(model_id)

            _ = asyncio.create_task(_publish_socket_cleanup(model_id))

            structured_logger.info(
                f"{model_id}:worker_shutdown_completed: {model_id} - "
                "SUCCESS (shutdown_type=fast_termination)"
            )
            return True

        except Exception as e:
            logger.warning(f"⚠️ Process termination failed for {model_id}: {e}")
            return False

    async def _standard_unload(self, model_id: str, supervisor) -> bool:
        """
        Perform standard graceful unload using event-driven socket cleanup.

        Gracefully terminates worker process and publishes SocketCleanupRequested
        event (non-blocking) for background socket cleanup. Allows worker time
        to finish current operations before termination.

        Invariant: event_published ∧ non_blocking ∧ graceful_termination

        Args:
            model_id: Model identifier to unload
            supervisor: Process supervisor for the worker

        Returns:
            True if unload successful, False otherwise

        Side Effects:
            - Terminates worker process gracefully (timeout=10s)
            - Publishes SocketCleanupRequested event (fire-and-forget)
            - Logs unload progress

        Note:
            This method does NOT wait for socket cleanup completion.
            Event handler executes asynchronously in background.
        """
        logger.info(f"🔄 Standard model unload for {model_id}")

        try:
            tracked_pid = self._get_tracked_worker_pid(supervisor)
            stopped = await supervisor.stop(timeout=10)
            if not stopped:
                logger.error(
                    f"❌ Standard termination returned unsuccessful for {model_id} "
                    f"(tracked_pid={tracked_pid})"
                )
                return False
            if not self._is_pid_gone(tracked_pid):
                logger.error(
                    f"❌ Tracked worker PID still alive after standard unload for "
                    f"{model_id} (tracked_pid={tracked_pid})"
                )
                return False

            logger.info(
                f"✅ Standard termination confirmed for {model_id} "
                f"(tracked_pid={tracked_pid})"
            )
            self._remove_supervisor(model_id)

            _ = asyncio.create_task(_publish_socket_cleanup(model_id))

            structured_logger.info(
                f"{model_id}:worker_shutdown_completed: {model_id} - "
                "SUCCESS (shutdown_type=standard_termination)"
            )
            return True

        except Exception as e:
            logger.error(f"❌ Standard termination failed for {model_id}: {e}")
            return False

    async def _finalize_failed_unload(self, model_id: str, force: bool) -> str:
        """Leave failed unloads in a terminal local state, never UNLOADING."""
        supervisor = self._controller._process_state.get_supervisor(model_id)
        tracked_pid = self._get_tracked_worker_pid(supervisor) if supervisor else None

        if supervisor is None or self._is_pid_gone(tracked_pid):
            self._remove_supervisor(model_id)
            self._mark_unloaded(model_id)
            await self._publish_resource_update()
            return "not_loaded"

        reason = "force_unload_failed" if force else "unload_failed"
        _get_resource_tracker().set_model_error(
            model_id,
            f"{reason}: worker still alive (tracked_pid={tracked_pid})",
        )
        await self._publish_resource_update()
        return "error"

    def _remove_supervisor(self, model_id: str):
        """Remove supervisor from tracking."""
        self._controller._process_state.remove_supervisor(model_id)
        self._controller._process_state.remove_socket_path(model_id)

    def _mark_unloaded(self, model_id: str) -> None:
        """Mark model as unloaded in resource tracker."""
        resource_tracker = _get_resource_tracker()
        resource_tracker.update_model_resources(model_id, 0, 0)
        resource_tracker.set_model_not_loaded(model_id, "unload_complete")

    async def _publish_resource_update(self):
        """Publish SYSTEM_RESOURCES_UPDATED event with current VRAM/RAM info."""
        try:
            resource_tracker = _get_resource_tracker()
            # get_system_resources() publishes SYSTEM_RESOURCES_UPDATED event internally
            await resource_tracker.get_system_resources()
            logger.debug("Published resource update after model unload")
        except Exception as e:
            logger.warning(f"⚠️ Failed to publish resource update: {e}")
