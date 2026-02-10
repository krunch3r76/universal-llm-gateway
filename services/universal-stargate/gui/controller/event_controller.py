"""
Event controller for processing universal_stargate events.

Phase 2: Request-based history navigation.
Accumulates events per request_id and stores completed requests for navigation.
"""

import json
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any

from universal_logging import get_logger

from ..model.data_structures import DisplayData, RequestState
from ..model.response_parser import ResponseParser
from .data_processor import DataProcessor
from .extension_manager import ExtensionManager

logger = get_logger(__name__)


class EventController:
    """Main controller for processing universal_stargate events.

    Phase 2 Architecture:
    - _active_requests: dict tracking in-progress requests (accumulating events)
    - history: deque of completed RequestState (not individual events)
    - _completed_request_ids: set tracking already-completed requests (duplicate guard)
    - Navigation operates on completed requests only

    Invariants:
    - ∀ r ∈ _active_requests: ¬r.is_complete
    - ∀ r ∈ history: r.is_complete
    - ∀ request_id: request_id ∈ _active_requests ⊕ request_id ∈ _completed_request_ids
    - len(history) ≤ 100 (request cap, not event cap)
    - history_index ∈ {-1} ∪ [0, len(history))
    """

    # Streaming display throttle: update view every N chunks
    _STREAMING_UPDATE_INTERVAL = 5

    def __init__(self, view_callback: Callable[[DisplayData], None]):
        """Initialize event controller."""
        self.view_callback = view_callback
        self.response_parser = ResponseParser()
        self.data_processor = DataProcessor()
        self.extension_manager = ExtensionManager()

        # Request-based history (Phase 2)
        # _active_requests: requests currently in progress (accumulating events)
        # history: completed requests (ready for navigation)
        # _completed_request_ids: guard against duplicate completion events
        self._active_requests: dict[str, RequestState] = {}
        self._completed_request_ids: set[str] = set()
        self.history: deque[RequestState] = deque(maxlen=100)  # 100 requests
        self.history_index: int = -1  # -1 = live mode
        self._navigation_callback: Callable[[int, int, bool, bool], None] | None = None

        # Track current live request for display
        self._current_live_request_id: str | None = None

        # Track if new requests arrived while browsing (for LIVE button highlight)
        self._new_requests_while_browsing: bool = False

        # Track streaming chunk count per request for throttled display updates
        self._chunk_counts: dict[str, int] = {}

    def process_event(self, event_data: dict[str, Any] | Any) -> None:
        """Process incoming event data (dict or MonitoringEvent).

        Accumulates events by request_id. Completes request on chat_completion.
        """
        try:
            # Convert to dict
            if isinstance(event_data, dict):
                event_dict = event_data
            elif hasattr(event_data, "model_dump"):
                event_dict = event_data.model_dump()
            else:
                msg = f"Expected dict or MonitoringEvent, got {type(event_data)}"
                raise ValueError(msg)

            event_type = event_dict.get("type", "unknown")
            request_id = event_dict.get("request_id") or event_dict.get("data", {}).get(
                "request_id"
            )

            # Log ALL event types to debug
            logger.debug(
                f"📨 Event received: type={event_type}, request_id={request_id}"
            )

            # Skip events without request_id (system events, etc.)
            if not request_id:
                logger.debug(f"Skipping event without request_id: {event_type}")
                return

            # Guard: Ignore duplicate completion events
            # BUT: Allow streaming chunks (they may arrive after chat_completion)
            if request_id in self._completed_request_ids and event_type not in {
                "streaming_chunk",
                "streaming_chunk_batch",
            }:
                logger.debug(
                    f"Ignoring {event_type} for already-completed request {request_id}"
                )
                return

            # Handle late-arriving chunks for completed requests
            if request_id in self._completed_request_ids and event_type in {
                "streaming_chunk",
                "streaming_chunk_batch",
            }:
                logger.debug(
                    f"Late {event_type} for completed request {request_id} - "
                    f"updating history entry"
                )
                self._handle_late_chunk(request_id, event_dict, event_type)
                return

            # Get or create request state
            is_new_request = request_id not in self._active_requests
            if is_new_request:
                self._active_requests[request_id] = RequestState(
                    request_id=request_id,
                    started_at=datetime.now(),
                )
                self._chunk_counts[request_id] = 0
                logger.info(f"📝 New request started: {request_id}")

            request_state = self._active_requests[request_id]

            # Accumulate event data into request state
            self._accumulate_event(request_state, event_dict, event_type)

            # LIVE mode: automatically follow new requests
            if self.history_index == -1:
                # In LIVE mode - auto-switch to new requests
                if is_new_request:
                    self._current_live_request_id = request_id
                    logger.info(f"🎯 LIVE mode: auto-switching to {request_id}")

                # Update display for current live request
                should_update = self._should_update_display(event_type, request_id)
                if should_update and request_id == self._current_live_request_id:
                    # Pass actual event_type to control which panels get updated
                    display_data = request_state.to_display_data(
                        self.response_parser, event_type=event_type
                    )
                    resp_len = len(display_data.response.formatted_text)
                    logger.debug(
                        f"🖥️  LIVE mode update: event={event_type}, "
                        f"response_len={resp_len} chars, "
                        f"chunks={len(request_state.response_chunks)}"
                    )
                    self.view_callback(display_data)
            else:
                # Browsing history - mark that new requests are available
                if is_new_request:
                    self._new_requests_while_browsing = True
                    self._notify_navigation_state()
                    logger.debug(
                        f"📢 New request {request_id} arrived while browsing - "
                        f"LIVE button should highlight"
                    )

            # Check for completion (chat_completion event signals end)
            if event_type == "chat_completion":
                resp_len = len(request_state.get_accumulated_response())
                logger.info(
                    f"🔔 chat_completion for {request_id}, response: {resp_len} chars"
                )
                self._complete_request(request_id)

        except Exception as e:
            logger.error(f"❌ Error processing event: {e}", exc_info=True)
            raw = event_data if isinstance(event_data, dict) else {}
            self._send_error_to_view(str(e), raw)

    def _should_update_display(self, event_type: str, request_id: str) -> bool:
        """Determine if view should update for this event.

        Throttles streaming_chunk events to prevent scroll reset spam.
        Updates every _STREAMING_UPDATE_INTERVAL chunks.
        """
        # Always update on significant events
        if event_type in {
            "request_info",
            "pre_processing",
            "chat_completion",
            "streaming_chunk_batch",
        }:
            logger.debug(f"✓ Update allowed: {event_type}")
            return True

        # Throttle individual streaming chunks
        if event_type == "streaming_chunk":
            self._chunk_counts[request_id] = self._chunk_counts.get(request_id, 0) + 1
            count = self._chunk_counts[request_id]
            should_update = count % self._STREAMING_UPDATE_INTERVAL == 0
            if should_update:
                logger.debug(f"✓ Update allowed: chunk #{count} (every 5)")
            return should_update

        logger.debug(f"✗ Update skipped: {event_type}")
        return False

    def _handle_late_chunk(
        self, request_id: str, event_dict: dict[str, Any], event_type: str
    ) -> None:
        """Handle streaming chunks that arrive after request completion.

        Due to timing, chat_completion can arrive before all streaming chunks.
        Find the request in history and update it with the chunk data.
        """
        # Find request in history
        history_request = None
        history_index_found = None
        for idx, req in enumerate(self.history):
            if req.request_id == request_id:
                history_request = req
                history_index_found = idx
                break

        if not history_request:
            logger.warning(
                f"Late {event_type} for {request_id} but not found in history"
            )
            return

        # Extract and accumulate chunk
        data = event_dict.get("data", event_dict)
        if event_type == "streaming_chunk":
            chunk = data.get("chunk", "")
            if chunk:
                history_request.response_chunks.append(chunk)
                logger.debug(
                    f"📝 Late chunk added to history: {len(chunk)} chars "
                    f"(total: {len(history_request.get_accumulated_response())} chars)"
                )
        elif event_type == "streaming_chunk_batch":
            content = data.get("content", "")
            if content:
                history_request.response_chunks.append(content)
                logger.debug(
                    f"📝 Late chunk batch added to history: {len(content)} chars"
                )

        # Refresh display if viewing this request (history or live mode)
        if self.history_index == history_index_found or (
            self.history_index == -1 and self._current_live_request_id == request_id
        ):
            # Pass actual event_type to prevent request panel updates
            display_data = history_request.to_display_data(
                self.response_parser, event_type=event_type
            )
            self.view_callback(display_data)
            mode = "LIVE" if self.history_index == -1 else f"idx {history_index_found}"
            logger.debug(f"🖥️  Refreshed display for late chunk ({mode})")

    def _accumulate_event(
        self,
        request_state: RequestState,
        event_dict: dict[str, Any],
        event_type: str,
    ) -> None:
        """Accumulate event data into request state.

        Different event types contribute different data:
        - request_info: original_request
        - pre_processing: original_request, modified_request, token_metrics
        - streaming_chunk: response chunks
        - streaming_chunk_batch: response chunks (batched)
        - chat_completion: final response, marks completion
        """
        data = event_dict.get("data", event_dict)

        if event_type == "request_info":
            # Early event with original request
            if data.get("original_request"):
                request_state.original_request = self._format_request(
                    data.get("original_request")
                )
            request_state.model_id = data.get("model_id", "")

        elif event_type == "pre_processing":
            # Has both original and modified requests
            if data.get("original_request"):
                request_state.original_request = self._format_request(
                    data.get("original_request")
                )
            if data.get("modified_request"):
                request_state.modified_request = self._format_request(
                    data.get("modified_request")
                )
            # Update event_info with processing details
            request_state.event_info.update(
                {
                    "type": "pre_processing",
                    "gateway": data.get("gateway_endpoint", ""),
                    "processing_time": f"{data.get('processing_time_ms', 0):.2f} ms",
                }
            )
            request_state.gateway = data.get("gateway_endpoint", "")

        elif event_type == "streaming_chunk":
            # Accumulate streaming chunk
            chunk = data.get("chunk", "")
            if chunk:
                request_state.response_chunks.append(chunk)
                total_chars = len(request_state.get_accumulated_response())
                logger.debug(
                    f"📝 Chunk added: {len(chunk)} chars "
                    f"(chunks: {len(request_state.response_chunks)}, "
                    f"total: {total_chars})"
                )

        elif event_type == "streaming_chunk_batch":
            # Accumulate batched chunks
            content = data.get("content", "")
            if content:
                request_state.response_chunks.append(content)

        elif event_type == "chat_completion":
            # Final response - marks completion
            request_state.is_complete = True
            request_state.completed_at = datetime.now()

            # Extract final response if available
            # For chat_completion, response can be at top level or nested
            response = data.get("response")

            # Extract content from response structure
            if response:
                if isinstance(response, dict):
                    # OpenAI response format: {choices: [{message: {content: "..."}}]}
                    if "choices" in response:
                        choices = response.get("choices", [])
                        if choices and isinstance(choices, list):
                            message = choices[0].get("message", {})
                            if isinstance(message, dict):
                                content = message.get("content", "")
                                if content:
                                    request_state.final_response = content
                    # Direct content field
                    elif "content" in response:
                        request_state.final_response = response["content"]
                elif isinstance(response, str):
                    request_state.final_response = response

            # If no final_response extracted, accumulated chunks will be used
            # (streaming responses accumulate chunks, completion just signals end)

            # Update event_info for completion
            request_state.event_info.update(
                {
                    "type": "chat_completion",
                    "id": request_state.request_id,
                    "timestamp": (
                        request_state.completed_at.isoformat()
                        if request_state.completed_at
                        else ""
                    ),
                }
            )

            accumulated = request_state.get_accumulated_response()
            logger.info(
                f"✅ Request completed: {request_state.request_id} "
                f"(response: {len(accumulated)} chars, "
                f"chunks: {len(request_state.response_chunks)}, "
                f"final_response: {len(request_state.final_response)} chars)"
            )

    def _format_request(self, request_data: Any) -> str:
        """Format request data for display."""
        if isinstance(request_data, str):
            return request_data
        elif isinstance(request_data, dict):
            return json.dumps(request_data, indent=2)
        else:
            return str(request_data)

    def _complete_request(self, request_id: str) -> None:
        """Move completed request from active to history.

        Always moves to history on chat_completion event (may have empty response
        for error cases or non-streaming responses that haven't arrived yet).
        """
        if request_id not in self._active_requests:
            logger.warning(
                f"⚠️  Request {request_id} already completed or not found. "
                f"Active requests: {list(self._active_requests.keys())}"
            )
            return

        request_state = self._active_requests.pop(request_id)
        request_state.is_complete = True

        # Mark as completed to prevent duplicate events from recreating it
        self._completed_request_ids.add(request_id)

        # Cleanup chunk count tracking
        self._chunk_counts.pop(request_id, None)

        # Log content status
        accumulated = request_state.get_accumulated_response()
        if not accumulated:
            logger.warning(
                f"⚠️  Request {request_id} completed with NO response content. "
                f"Chunks: {len(request_state.response_chunks)}, "
                f"Final: {len(request_state.final_response)} chars. "
                f"Adding to history anyway (may be error case)."
            )

        # Track if history is full - the oldest request will be dropped
        dropped_request_id = None
        if len(self.history) >= (self.history.maxlen or 0):
            # Deque is full, oldest request will be dropped
            dropped_request_id = self.history[0].request_id

        # Add to history (deque automatically drops oldest when maxlen reached)
        self.history.append(request_state)

        # Remove dropped request_id from completed set to allow reuse
        if dropped_request_id:
            self._completed_request_ids.discard(dropped_request_id)
            logger.debug(
                f"🗑️  Dropped oldest request from history: {dropped_request_id}"
            )

        # Notify navigation UI
        self._notify_navigation_state()

        logger.info(
            f"📚 Request moved to history: {request_id} "
            f"(history size: {len(self.history)}, response: {len(accumulated)} chars)"
        )

    def navigate_back(self) -> bool:
        """Navigate to previous (older) completed request.

        Returns:
            True if navigation occurred, False if at oldest or empty
        """
        if not self.history:
            return False

        if self.history_index == -1:
            # Live → go to most recent completed request
            self.history_index = len(self.history) - 1
        elif self.history_index > 0:
            # In history → go to older request
            self.history_index -= 1
        else:
            # Already at oldest
            return False

        # Display the historical request
        request_state = self.history[self.history_index]
        display_data = request_state.to_display_data(self.response_parser)
        self.view_callback(display_data)
        self._notify_navigation_state()
        return True

    def navigate_forward(self) -> bool:
        """Navigate to next (newer) completed request or return to live.

        Returns:
            True if navigation occurred, False if already live
        """
        if self.history_index == -1:
            return False

        if self.history_index < len(self.history) - 1:
            # Move to newer request
            self.history_index += 1
            request_state = self.history[self.history_index]
            display_data = request_state.to_display_data(self.response_parser)
            self.view_callback(display_data)
        else:
            # At newest → return to live mode
            self.history_index = -1
            self._new_requests_while_browsing = False  # Clear flag
            self._show_live_or_latest()

        self._notify_navigation_state()
        return True

    def go_live(self) -> None:
        """Return to live mode."""
        if self.history_index != -1:
            self.history_index = -1
            self._new_requests_while_browsing = False  # Clear flag
            self._show_live_or_latest()
            self._notify_navigation_state()

    def _show_live_or_latest(self) -> None:
        """Show current live request or latest completed request.

        Priority order:
        1. Any active request (prefer most recent)
        2. Most recent completed request from history (history[-1])
        """
        # Priority 1: Show ANY active request
        if self._active_requests:
            # Prefer the current live request if it's still active
            if (
                self._current_live_request_id
                and self._current_live_request_id in self._active_requests
            ):
                request_id = self._current_live_request_id
            else:
                # Show the most recent active request (last one added)
                request_id = list(self._active_requests.keys())[-1]
                self._current_live_request_id = request_id

            request_state = self._active_requests[request_id]
            display_data = request_state.to_display_data(self.response_parser)
            self.view_callback(display_data)
            logger.debug(f"🖥️  LIVE mode: showing active request {request_id}")
            return

        # Priority 2: Show most recent from history (no active requests)
        if self.history:
            # Always show the most recent request (history[-1])
            request_state = self.history[-1]
            self._current_live_request_id = request_state.request_id
            display_data = request_state.to_display_data(self.response_parser)
            self.view_callback(display_data)
            logger.debug(
                f"🖥️  LIVE mode: showing most recent from history "
                f"({request_state.request_id})"
            )

    def set_navigation_callback(
        self, callback: Callable[[int, int, bool, bool], None] | None
    ) -> None:
        """Set callback for navigation state updates.

        Args:
            callback: Function(display_index, total_count, is_live, has_new_requests)
        """
        self._navigation_callback = callback

    def _notify_navigation_state(self) -> None:
        """Notify UI of current navigation state."""
        if self._navigation_callback:
            is_live = self.history_index == -1
            display_index = 0 if is_live else self.history_index + 1
            has_new = self._new_requests_while_browsing and not is_live
            self._navigation_callback(
                display_index, len(self.history), is_live, has_new
            )

    def get_history_stats(self) -> dict[str, int | bool]:
        """Get current history statistics."""
        return {
            "completed_requests": len(self.history),
            "active_requests": len(self._active_requests),
            "max_size": self.history.maxlen or 0,
            "current_index": self.history_index,
            "is_live": self.history_index == -1,
        }

    def _send_error_to_view(self, error_msg: str, raw_data: dict[str, Any]) -> None:
        """Send error information to view."""
        try:
            error_display = DisplayData(
                original_request="Error processing event",
                modified_request="Error processing event",
                response=self.response_parser.parse(
                    {
                        "error": error_msg,
                        "raw_data": (
                            str(raw_data)[:500] + "..."
                            if len(str(raw_data)) > 500
                            else str(raw_data)
                        ),
                    }
                ),
                event_info={
                    "id": "error",
                    "timestamp": "N/A",
                    "type": "error",
                    "processing_time": "N/A",
                    "gateway": "N/A",
                    "actions": f"Error: {error_msg}",
                },
            )
            self.view_callback(error_display)
        except Exception as e:
            logger.error(f"Failed to send error to view: {e}")

    def get_extension_manager(self) -> ExtensionManager:
        """Get the extension manager for registering extensions."""
        return self.extension_manager

    def get_stats(self) -> dict[str, Any]:
        """Get controller statistics."""
        return {
            "extensions": self.extension_manager.get_extension_count(),
            "parser_type": type(self.response_parser).__name__,
            "processor_type": type(self.data_processor).__name__,
            "history_stats": self.get_history_stats(),
        }
