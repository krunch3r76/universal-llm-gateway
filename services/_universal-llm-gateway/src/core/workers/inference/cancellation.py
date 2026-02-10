"""Cancellation logic for streaming inference."""

from universal_logging import get_logger

logger = get_logger(__name__)


class InferenceCancellationManager:
    """
    Manages cancellation of streaming inference operations.

    Provides unified cancellation API for both specific correlation IDs
    and current stream cancellation.
    """

    def __init__(self, process_state):
        """
        Initialize cancellation manager.

        Args:
            process_state: ProcessState containing supervisor references
        """
        self._process_state = process_state

    async def cancel_streaming_inference(
        self, model_id: str, stream_id: str, reason: str = "explicit_cancellation"
    ) -> bool:
        """
        Cancel an active streaming inference.

        Args:
            model_id: Model identifier
            stream_id: Stream ID of the stream to cancel (formerly correlation_id)
            reason: Reason for cancellation

        Returns:
            bool: True if cancellation was successful
        """
        try:
            supervisor = self._process_state.get_supervisor(model_id)
            if not supervisor:
                logger.warning(f"No supervisor found for model {model_id}")
                return False

            # Use Universal Protocol RPC for cancellation
            if not supervisor._http_client:
                logger.error(f"HTTP client not initialized for model {model_id}")
                return False

            response = await supervisor._inference_rpc_call(
                "cancel_inference",
                {"stream_id": stream_id, "reason": reason},
                timeout=10.0,
            )

            logger.info(
                f"🔧 [controller] Stream cancellation response for {model_id}/{stream_id}: {response}"
            )

            return response.get("success", False)

        except Exception as e:
            logger.error(
                f"❌ [controller] Failed to cancel stream {stream_id} on {model_id}: {e}"
            )
            return False

    async def cancel_current_stream(self, model_id: str) -> bool:
        """
        Cancel the current stream for a model (when stream ID is unknown).

        Note: With Universal Protocol, we track streams by stream_id.
        This method attempts to cancel all active streams for a model.

        Args:
            model_id: Model identifier

        Returns:
            bool: True if cancellation was successful
        """
        try:
            supervisor = self._process_state.get_supervisor(model_id)
            if not supervisor:
                logger.warning(f"No supervisor found for model {model_id}")
                return False

            # Get all active streams for this supervisor
            active_streams = list(supervisor._active_streams)

            if not active_streams:
                logger.info(f"No active streams for model {model_id}")
                return True  # No streams to cancel is considered success

            # Cancel all active streams
            success = True
            for stream_id in active_streams:
                try:
                    if not supervisor._http_client:
                        logger.error(
                            f"HTTP client not initialized for model {model_id}"
                        )
                        success = False
                        continue

                    response = await supervisor._inference_rpc_call(
                        "cancel_inference", {"stream_id": stream_id}, timeout=10.0
                    )

                    if not response.get("success", False):
                        success = False

                except Exception as e:
                    logger.error(f"Failed to cancel stream {stream_id}: {e}")
                    success = False

            logger.info(
                f"🔧 [controller] Current stream cancellation for {model_id}: {'success' if success else 'partial/failed'}"
            )
            return success

        except Exception as e:
            logger.error(
                f"❌ [controller] Failed to cancel current stream on {model_id}: {e}"
            )
            return False

    async def cancel_work(
        self,
        model_id: str,
        stream_id: str | None = None,
        reason: str = "explicit_cancellation",
    ) -> bool:
        """
        Cancel active work on a model (unified cancellation API).

        This is the primary method for cancelling work. It handles both
        specific stream IDs and current work cancellation.

        Args:
            model_id: Model identifier
            stream_id: Optional stream ID for specific stream (formerly correlation_id)
            reason: Reason for cancellation

        Returns:
            bool: True if cancellation was successful
        """
        if stream_id:
            return await self.cancel_streaming_inference(model_id, stream_id, reason)
        else:
            return await self.cancel_current_stream(model_id)
