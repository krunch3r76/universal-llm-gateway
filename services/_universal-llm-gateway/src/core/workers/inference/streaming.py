"""Streaming inference operations with guaranteed cancellation.

This module provides robust streaming inference with:
- Guaranteed stream cancellation via async context manager
- Worker crash detection via event bus
- Explicit timeout handling
- Clean separation of concerns (no legacy adapters)
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from process_ipc import ProcessSupervisor
from universal_logging import get_logger

from ..process.state import ProcessState

logger = get_logger(__name__)
structured_logger = get_logger("universal_llm_gateway.inference")


def parse_stream_message(response_data: dict[str, Any]) -> tuple[str, Any]:
    """
    Parse streaming message from DATA_STREAM signal.

    Args:
        response_data: Data from DATA_STREAM payload

    Returns:
        (status, data) tuple:
        - ("complete", None) - stream done
        - ("error", {"message": str, "code": str}) - stream error
        - ("chunk", chunk_dict) - regular chunk

    Raises:
        ValueError: If response_data format is invalid
    """
    if not isinstance(response_data, dict):
        raise ValueError(f"Invalid response_data type: {type(response_data)}")

    status = response_data.get("status")
    if status == "complete":
        return ("complete", None)
    elif status == "error":
        return (
            "error",
            {
                "message": response_data.get("error", "Unknown error"),
                "code": response_data.get("error_code", "engine_error"),
            },
        )
    else:
        return ("chunk", response_data)


@asynccontextmanager
async def managed_stream(
    supervisor: ProcessSupervisor, command: dict[str, Any]
) -> AsyncIterator[tuple[str, Callable[[], None]]]:
    """
    Context manager for streaming with automatic cleanup.

    Ensures cancellation command is sent when exiting the stream context,
    whether due to normal completion, client disconnect, or error.

    Features:
    - Returns stream_id for WebSocket connection
    - Returns mark_completed function to signal clean completion
    - Sends cancel_inference RPC on cleanup (only if not marked complete)
    - Stops stream tracking in supervisor

    Args:
        supervisor: ProcessSupervisor instance
        command: Command to start the stream

    Yields:
        (stream_id, mark_completed): Stream identifier and completion marker function
    """
    stream_id = None
    completed_cleanly = False

    def mark_completed():
        """Mark the stream as completed cleanly."""
        nonlocal completed_cleanly
        completed_cleanly = True

    try:
        # DIAGNOSTIC: Log exact command being sent to start_stream()
        logger.info(f"🔍 [managed_stream] Calling start_stream with command: {command}")

        # Start stream and get stream info
        stream_info = await supervisor.start_stream(command)
        stream_id = stream_info["stream_id"]
        websocket_path = stream_info["websocket_path"]
        logger.info(
            f"🔍 [managed_stream] Started stream {stream_id} at {websocket_path}"
        )
        logger.info(f"🔍 [managed_stream] Stream info received: {stream_info}")
        yield (stream_id, mark_completed)

    finally:
        # CRITICAL: Nest cleanup to ensure stop_stream always executes
        # even if CancelledError is raised during RPC cancellation
        if stream_id:
            logger.info(
                f"🧹 [managed_stream] Cleaning up stream {stream_id} (completed_cleanly={completed_cleanly})"
            )

            try:
                # Only send cancellation if stream didn't complete cleanly
                # If completed_cleanly=True, the WebSocket handler already cleaned up ACTIVE_STREAMS
                # If completed_cleanly=False, we need to call cancel_inference to clean up
                if not completed_cleanly:
                    if supervisor._http_client is not None:
                        try:
                            # CRITICAL: Shield from CancelledError to ensure cleanup completes
                            # Without shield, the await can be cancelled before RPC executes,
                            # leaving stale streams in ACTIVE_STREAMS on the worker
                            await asyncio.shield(
                                supervisor._inference_rpc_call(
                                    "cancel_inference", {"stream_id": stream_id}
                                )
                            )
                            logger.info(
                                f"✅ [managed_stream] Sent cancellation for {stream_id}"
                            )
                        except asyncio.CancelledError:
                            # Even if outer task is cancelled, we tried to send the RPC
                            logger.info(
                                f"⚠️  [managed_stream] Cleanup interrupted but RPC was shielded for {stream_id}"
                            )
                            raise  # Re-raise after logging
                        except Exception as e:
                            logger.warning(
                                f"⚠️  [managed_stream] Failed to cancel inference {stream_id}: {e}"
                            )
                    else:
                        # Worker has crashed, HTTP client is gone
                        logger.info(
                            f"⚠️  [managed_stream] Skipping RPC cancellation for {stream_id} (worker crashed, client unavailable)"
                        )
                else:
                    logger.info(
                        f"✅ [managed_stream] Stream {stream_id} completed cleanly, WebSocket handler cleaned up"
                    )
            finally:
                # CRITICAL: This must execute even if CancelledError was raised above
                try:
                    # Stop tracking the stream
                    supervisor.stop_stream(stream_id)
                    logger.info(
                        f"✅ [managed_stream] Stopped tracking stream {stream_id}"
                    )
                except Exception as e:
                    logger.error(
                        f"❌ [managed_stream] Failed to stop tracking stream {stream_id}: {e}"
                    )


class StreamingInferenceManager:
    """
    Manages streaming inference operations with robust cancellation and crash detection.

    Features:
    - Guaranteed stream cancellation via managed_stream context manager
    - Event-driven worker crash detection
    - Explicit error handling (no silent fallbacks)
    - Clean async/await patterns
    """

    def __init__(
        self,
        process_state: ProcessState,
        gateway_config: Any,
        event_bus: Any | None = None,
    ):
        """
        Initialize the streaming inference manager.

        Args:
            process_state: Process state containing supervisor references
            gateway_config: Gateway configuration for timeouts
            event_bus: Optional event bus for crash detection

        Raises:
            ValueError: If process_state or gateway_config is None
        """
        if process_state is None:
            raise ValueError("process_state cannot be None")
        if gateway_config is None:
            raise ValueError("gateway_config cannot be None")

        self._process_state = process_state
        self._gateway_config = gateway_config
        self._event_bus = event_bus

    async def inference_stream(
        self,
        model_id: str,
        messages: list[dict[str, str]] | str,
        parameters: dict[str, Any],
        correlation_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Handles streaming inference as an async generator, now with robust cancellation.
        """
        logger.info(
            f"🔧 [controller] Starting event-driven inference_stream for {model_id}"
        )

        crash_detected = asyncio.Event()
        crash_error_message = None

        async def crash_event_handler(event):
            nonlocal crash_error_message
            crash_error_message = event.payload.get(
                "error_message", "Worker process crashed"
            )
            logger.error(
                f"🚨 [controller] WORKER_CRASH_DETECTED event received for {model_id}: {crash_error_message}"
            )
            crash_detected.set()

        subscription = None
        if self._event_bus:
            try:
                from src.core.events import WORKER_CRASH_DETECTED

                subscription = self._event_bus.subscribe_async(
                    WORKER_CRASH_DETECTED,
                    crash_event_handler,
                    payload_match={"model_id": model_id},
                )
                logger.info(
                    f"✅ [controller] Subscribed to WORKER_CRASH_DETECTED for {model_id}"
                )
            except Exception as e:
                logger.error(
                    f"❌ [controller] Failed to subscribe to crash events for {model_id}: {e}"
                )

        try:
            # Build command with flat parameter structure (RPC params expect flat dict)
            command = parameters.copy() if parameters else {}

            # Extract timeout hint from parameters (_timeout_hint from upstream)
            timeout_hint = command.pop("_timeout_hint", None)

            # Add prompt or messages at top level
            if isinstance(messages, str):
                command["prompt"] = messages
            else:
                command["messages"] = messages

            # Add correlation_id if provided (will be filtered out at worker level)
            if correlation_id:
                command["correlation_id"] = correlation_id

            # Get default streaming timeout from configuration
            default_timeout = self._gateway_config.streaming.timeout

            # Use timeout hint if provided, otherwise use config default
            timeout = timeout_hint if timeout_hint is not None else default_timeout

            # Pass timeout to worker as hint for deadline enforcement
            command["timeout_hint"] = timeout

            # DIAGNOSTIC: Log command structure before entering context manager
            logger.info(
                f"🔧 [controller] Built command for {model_id}: "
                f"messages={'<str>' if isinstance(messages, str) else f'{len(messages)} messages'}, "
                f"timeout={timeout}, "
                f"params_keys={list(command.keys())}"
            )
            # Log first message content if available (for debugging empty responses)
            if isinstance(messages, list) and messages:
                first_msg = messages[0]
                logger.info(
                    f"🔧 [controller] First message: role={first_msg.get('role')}, "
                    f"content_length={len(first_msg.get('content', ''))}"
                )

            supervisor = self._process_state.get_supervisor(model_id)
            if not supervisor:
                raise RuntimeError(f"No supervisor found for model {model_id}")

            # Use the context manager to guarantee cleanup on disconnect
            async with managed_stream(supervisor, command) as (
                stream_id,
                mark_completed,
            ):
                yield {"stream_id": stream_id, "_type": "stream_id"}

                # Connect to WebSocket for streaming
                from universal_protocol.server.uds_security import (
                    socket_path_for_worker,
                )
                from universal_protocol.ws.client import StreamClient

                # Get worker socket path from state or use config-based fallback
                worker_socket_path = self._process_state.get_socket_path(model_id)
                if not worker_socket_path:
                    # Fallback to config-based path using socket_path_for_worker
                    try:
                        worker_id_int = int(model_id)
                    except ValueError:
                        # For non-numeric IDs, use a stable hash
                        worker_id_int = hash(model_id) % 1000000
                    worker_socket_path = socket_path_for_worker(worker_id_int)

                # Create WebSocket client with correct API
                # Use streaming timeout from config (default 300s) instead of StreamClient default (30s)
                # This allows slow-TTFT models (e.g., large models with long compile times)
                # to stream without timing out before the first token
                stream_client = StreamClient(
                    worker_socket_path, stream_id, timeout=timeout
                )

                # Track clean completion - used for post-loop crash detection
                was_completed = False

                def mark_completed_and_flag():
                    """Mark stream as completed and set completion flag."""
                    nonlocal was_completed
                    was_completed = True
                    mark_completed()

                try:
                    # Connect to stream
                    logger.info(
                        f"🔌 [streaming] Connecting to WebSocket: socket={worker_socket_path}, stream_id={stream_id}"
                    )
                    await stream_client.connect()
                    logger.info(
                        f"🔌 [streaming] WebSocket connected for stream {stream_id}"
                    )

                    # Create task for crash detection
                    crash_task = asyncio.create_task(crash_detected.wait())

                    try:
                        # Use async iterator to get messages
                        import time

                        last_msg_time = time.perf_counter()
                        msg_count = 0

                        async for message in stream_client.iter_messages():
                            # Log WebSocket message reception timing
                            current_time = time.perf_counter()
                            time_since_last = (
                                current_time - last_msg_time
                            ) * 1000  # ms
                            msg_count += 1

                            # DIAGNOSTIC: Log every message for debugging
                            logger.info(
                                f"📨 [streaming] Message #{msg_count} ({time_since_last:.1f}ms since last): {message}"
                            )

                            last_msg_time = current_time

                            # Check for crash
                            if crash_task.done():
                                raise RuntimeError(
                                    crash_error_message or "Worker process crashed"
                                )

                            # Handle different frame types from worker
                            frame_type = message.get("t")
                            if frame_type == "done":
                                # Stream completed cleanly - mark as completed
                                mark_completed_and_flag()
                                # Yield usage info
                                usage = message.get("usage", {})
                                yield {"finish_reason": "stop", "usage": usage}
                                break
                            elif frame_type == "err":
                                # Stream error - preserve full error envelope for proper propagation
                                error_msg = message.get("message", "Unknown error")
                                error_code = message.get("code", "STREAM_ERROR")
                                message.get("source", "stream")
                                error_data = message.get("data", {})

                                # Import StreamError for structured error handling
                                from universal_protocol.errors import StreamError

                                raise StreamError(
                                    code=error_code, message=error_msg, data=error_data
                                )
                            elif frame_type == "token":
                                # Regular token - yield the chunk
                                chunk_data = {
                                    "choices": [
                                        {
                                            "text": message.get("txt", ""),
                                            "index": 0,
                                            "finish_reason": message.get(
                                                "finish_reason"
                                            ),
                                        }
                                    ]
                                }
                                # Add token index if available
                                if "i" in message:
                                    chunk_data["token_index"] = message["i"]

                                message.get("txt", "")[:20]

                                yield chunk_data

                                # Check if this chunk has finish_reason - indicates stream completion
                                finish_reason = message.get("finish_reason")
                                if finish_reason:
                                    logger.info(
                                        f"✅ [streaming] Stream completed with finish_reason: {finish_reason}"
                                    )
                                    mark_completed_and_flag()
                                    break
                            else:
                                # Unknown frame type - log and continue
                                logger.warning(f"Unknown SSE frame type: {frame_type}")

                    except asyncio.CancelledError:
                        # Client disconnected - cancel crash task and propagate immediately
                        crash_task.cancel()
                        # Don't wait for cancellation - propagate immediately so new requests can start
                        raise  # Re-raise to propagate cancellation immediately
                    finally:
                        # Post-loop crash detection: Check if worker crashed after loop exit
                        # This catches crashes that close the WebSocket before we detect them in-loop
                        if not was_completed and crash_detected.is_set():
                            error_msg = (
                                crash_error_message
                                or f"Worker process for {model_id} crashed during streaming"
                            )
                            logger.error(
                                f"🚨 [streaming] Worker crash detected after stream loop exit: {error_msg}"
                            )
                            raise RuntimeError(error_msg)
                finally:
                    # Ensure WebSocket is closed
                    try:
                        await stream_client.disconnect()
                    except Exception:
                        pass

        except asyncio.CancelledError:
            logger.info(
                f"🔌 [streaming] Client disconnected during stream for {model_id}. Cleanup is guaranteed by context manager."
            )
            raise
        except Exception as e:
            logger.error(
                f"❌ Error during inference_stream for {model_id}: {e}", exc_info=True
            )
            raise
        finally:
            # Event bus subscription cleanup is automatic in universal_event_bus v0.2.0+
            if subscription:
                logger.debug(
                    f"✅ [controller] Crash detection subscription cleaned up for {model_id}"
                )
