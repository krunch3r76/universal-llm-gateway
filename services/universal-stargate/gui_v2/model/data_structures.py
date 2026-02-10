"""
Data structures for session management and event tracking.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass
class RequestSession:
    """
    Represents a complete chat completion session including all related events.

    Attributes:
        id: Unique session identifier (from request_info event)
        timestamp: When the session was created
        status: Current session status
        original_request: Original request data
        modified_request: Modified request after preprocessing
        events: Chronological list of all events for this session
        response_chunks: List of streaming response chunks
        complete_response: Combined response (available when complete)
        metadata: Additional session metadata
        processing_time_ms: Total processing time in milliseconds
    """

    id: str
    timestamp: datetime
    status: Literal["pending", "processing", "streaming", "complete", "error"]
    original_request: dict[str, Any]
    modified_request: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    response_chunks: list[dict[str, Any]] = field(default_factory=list)
    complete_response: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    processing_time_ms: float | None = None

    def add_event(self, event: dict[str, Any]) -> None:
        """
        Add event to session history and update status.

        Args:
            event: Event data dictionary
        """
        self.events.append(event)
        signal = event.get("signal", "")

        if signal == "monitoring.request_info":
            self.update_status("pending")
            self.original_request = event.get("request", {})

        elif signal == "monitoring.chat_completion":
            self.update_status("processing")
            self.modified_request = event.get("request", {})

        elif signal == "monitoring.streaming_chunk":
            if self.status != "streaming":
                self.update_status("streaming")
            self.add_chunk(event)

        elif signal == "monitoring.error":
            self.update_status("error")
            self.metadata["error"] = event.get("error", "Unknown error")

    def add_chunk(self, chunk: dict[str, Any]) -> None:
        """
        Add streaming chunk and update complete response.

        Args:
            chunk: Streaming chunk event data
        """
        self.response_chunks.append(chunk)

        # Extract chunk content
        content = chunk.get("chunk", {}).get("content", "")
        if not self.complete_response:
            self.complete_response = content
        else:
            self.complete_response += content

        # Check if this is the final chunk
        if chunk.get("chunk", {}).get("finish_reason") == "stop":
            self.update_status("complete")
            if "start_time" in self.metadata:
                end_time = datetime.now()
                start_time = self.metadata["start_time"]
                self.processing_time_ms = (end_time - start_time).total_seconds() * 1000

    def update_status(self, new_status: str) -> None:
        """
        Update session status with appropriate transitions.

        Args:
            new_status: New status to set
        """
        valid_transitions = {
            None: {"pending"},
            "pending": {"processing", "error"},
            "processing": {"streaming", "error"},
            "streaming": {"complete", "error"},
            "complete": {"error"},  # Only allow transition to error
            "error": set(),  # No transitions out of error
        }

        current = self.status if hasattr(self, "status") else None
        if new_status in valid_transitions.get(current, set()):
            self.status = new_status
            if new_status == "processing" and "start_time" not in self.metadata:
                self.metadata["start_time"] = datetime.now()
        else:
            logger.warning(f"Invalid status transition: {current} -> {new_status}")

    def is_complete(self) -> bool:
        """
        Check if session is complete.

        Returns:
            True if status is 'complete' or 'error'
        """
        return self.status in ("complete", "error")

    def get_summary(self) -> str:
        """
        Get short summary for list display.

        Returns:
            Brief summary of the session
        """
        # Get the first line or part of the request
        request_str = str(
            self.original_request.get("messages", [{}])[-1].get("content", "")
        )
        if len(request_str) > 60:
            request_str = request_str[:57] + "..."

        status_symbols = {
            "pending": "⏳",
            "processing": "🔄",
            "streaming": "📝",
            "complete": "✅",
            "error": "❌",
        }

        status_symbol = status_symbols.get(self.status, "?")
        timestamp_str = self.timestamp.strftime("%H:%M:%S")

        return f"{status_symbol} [{timestamp_str}] {request_str}"
