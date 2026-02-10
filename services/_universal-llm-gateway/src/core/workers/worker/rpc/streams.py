"""Stream management RPC handlers."""

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger
from universal_protocol.errors import EngineError

if TYPE_CHECKING:
    from universal_protocol.ws.registry import StreamEntry

logger = get_logger(__name__)

# Cleanup completion timeout configuration
CLEANUP_COMPLETION_TIMEOUT_S = 2.0
CLEANUP_COMPLETION_TIMEOUT_S_MAX = 5.0
CLEANUP_COMPLETION_TIMEOUT_S_MIN = 0.1

# Forced cancellation configuration
CANCELLATION_GRACE_PERIOD_S = 0.5  # Time to wait for cooperative cancellation


class StreamHandlers:
    """Mix-in class for stream management RPC handlers."""

    def _require_stream_id(self, params: dict) -> str:
        """
        Extract and validate stream_id from params.

        Args:
            params: RPC parameters

        Returns:
            Validated stream_id

        Raises:
            EngineError: If stream_id missing or invalid
        """
        stream_id = params.get("stream_id")
        if not stream_id:
            raise EngineError(code="INVALID_PARAMS", message="stream_id is required")
        return stream_id

    def _cancel_stream(self, entry: "StreamEntry", stream_id: str) -> None:
        """Signal stream cancellation.

        Responsibility: Emit cancellation signal only.

        Side-effects:
            Sets cancellation_event on entry
        """
        entry.cancel()
        logger.debug(f"🛑 [worker] Signaled cancellation for stream {stream_id}")

    def _force_cancel_task_if_needed(
        self, entry: "StreamEntry", stream_id: str
    ) -> bool:
        """Force-cancel streaming task if it exists and is running.

        Responsibility: Check task existence and force cancellation only.

        Inputs:
            entry: StreamEntry with potential task reference
            stream_id: For logging

        Outputs:
            True if task was force-cancelled, False otherwise

        Side-effects:
            Calls task.cancel() if task exists and is not done
        """
        if not entry.task:
            return False

        if entry.task.done():
            logger.debug(f"[worker] Task already completed for stream {stream_id}")
            return False

        logger.info(
            f"⚠️ [worker] Force-cancelling unresponsive task for stream {stream_id}"
        )
        entry.task.cancel()
        return True

    async def _await_stream_cleanup(
        self, entry: "StreamEntry", timeout: float, stream_id: str
    ) -> bool:
        """Wait for stream cleanup with cooperative then forced cancellation.

        Responsibility: Orchestrate cooperative wait → forced cancel → final wait.

        Inputs:
            entry: StreamEntry
            timeout: Total seconds to wait (already clamped)
            stream_id: For logging

        Outputs:
            True if cleanup completed, False if timeout

        Flow:
            1. Wait CANCELLATION_GRACE_PERIOD_S for cooperative cancellation
            2. If not complete, force-cancel task
            3. Continue waiting for remaining timeout
        """
        # Phase 1: Cooperative cancellation (grace period)
        grace_period = min(CANCELLATION_GRACE_PERIOD_S, timeout)
        success = await entry.wait_for_cleanup(grace_period)
        if success:
            logger.debug(
                f"✅ [worker] Cleanup completed cooperatively for stream {stream_id}"
            )
            return True

        # Phase 2: Force-cancel if still running
        forced = self._force_cancel_task_if_needed(entry, stream_id)
        if forced:
            logger.debug(
                f"[worker] Forced cancellation initiated for stream {stream_id}, "
                "waiting for cleanup signal"
            )

        # Phase 3: Wait for remaining timeout
        remaining = timeout - grace_period
        if remaining > 0:
            success = await entry.wait_for_cleanup(remaining)
            if success:
                logger.debug(
                    f"✅ [worker] Cleanup completed after forced cancellation "
                    f"for stream {stream_id}"
                )
                return True

        logger.warning(
            f"⚠️ [worker] Cleanup timeout ({timeout}s) for stream {stream_id}"
        )
        return False

    async def _finalize_stream_cleanup(self, stream_id: str, reason: str) -> None:
        """Remove stream from registry.

        Responsibility: Delegate to cleanup_stream_entry only.
        """
        from ..stream_lifecycle import cleanup_stream_entry

        await cleanup_stream_entry(stream_id, reason=reason)

    def _schedule_deferred_cleanup(
        self, entry: "StreamEntry", stream_id: str, reason: str
    ) -> None:
        """Schedule background cleanup after timeout.

        Responsibility: Fire-and-forget cleanup task only.

        Side-effects:
            Creates background asyncio task
        """

        async def deferred_cleanup() -> None:
            try:
                # Wait indefinitely for cleanup signal
                await entry.cleanup_complete_event.wait()
                logger.info(
                    f"✅ [worker] Deferred cleanup signal received for {stream_id}"
                )
                await self._finalize_stream_cleanup(stream_id, reason)
            except Exception as e:
                logger.error(
                    f"❌ [worker] Deferred cleanup failed for {stream_id}: {e}"
                )

        task = asyncio.create_task(deferred_cleanup())
        task.add_done_callback(
            lambda t: logger.debug(f"Deferred cleanup task done for {stream_id}")
            if not t.exception()
            else logger.error(
                f"Deferred cleanup task exception for {stream_id}: {t.exception()}"
            )
        )

    async def handle_cancel_inference(self, params: dict) -> dict:
        """Handle cancel_inference RPC request.

        Orchestrates: validate → lookup → cancel → wait → cleanup or defer

        Invariant:
          ∀ stream_id, handle_cancel_inference returns ⟹
            (cleanup_complete_event.is_set() ∧ stream_id ∉ stream_registry)
            ∨ (¬cleanup_complete_event.is_set() ∧ stream_id ∈ stream_registry
               ∧ deferred_cleanup_pending)
        """
        from universal_protocol.ws.registry import stream_registry

        # 1. Validate
        stream_id = self._require_stream_id(params)

        # 2. Lookup entry
        entry = stream_registry.get(stream_id)
        if not entry:
            logger.info(f"⚠️ [worker] Stream {stream_id} not found in registry")
            return {"success": True}

        # 3. Signal cancellation
        self._cancel_stream(entry, stream_id)

        # 4. Clamp timeout
        raw_timeout = params.get("cleanup_timeout", CLEANUP_COMPLETION_TIMEOUT_S)
        timeout = max(
            CLEANUP_COMPLETION_TIMEOUT_S_MIN,
            min(raw_timeout, CLEANUP_COMPLETION_TIMEOUT_S_MAX),
        )

        # 5. Await cleanup with bounded timeout
        cleanup_success = await self._await_stream_cleanup(entry, timeout, stream_id)

        # 6. Finalize or defer
        if cleanup_success:
            await self._finalize_stream_cleanup(stream_id, reason="cancel")
            logger.info(f"✅ [worker] Cancelled stream {stream_id}")
        else:
            # Keep entry in registry, schedule deferred cleanup
            self._schedule_deferred_cleanup(entry, stream_id, reason="cancel_timeout")
            logger.warning(
                f"⚠️ [worker] Stream {stream_id} cleanup timeout, "
                "deferred cleanup scheduled"
            )

        return {"success": True}
