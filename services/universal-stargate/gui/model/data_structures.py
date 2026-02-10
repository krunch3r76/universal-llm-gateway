"""
Data structures for universal_stargate monitoring events.

Defines the core data classes used throughout the GUI for representing
universal_stargate events, parsed responses, and display-ready data.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .response_parser import ResponseParser


@dataclass
class StargateEvent:
    """Core data structure for universal_stargate monitoring events"""

    id: str
    timestamp: datetime
    event_type: str
    original_request: dict[str, Any]
    modified_request: dict[str, Any]
    stargate_actions: list[str]
    processing_time_ms: float
    gateway_endpoint: str
    token_metrics: dict[str, Any] | None = None
    model_metadata: dict[str, Any] | None = None
    response_data: dict[str, Any] | None = None
    request_id: str | None = None


@dataclass
class ParsedResponse:
    """Parsed response data with display formatting"""

    raw_data: dict[str, Any]
    formatted_text: str
    response_type: str  # 'json', 'streaming', 'error', 'empty', 'complete'
    is_streaming: bool
    chunks: list[str] | None = None
    error_message: str | None = None


@dataclass
class DisplayData:
    """Data prepared for view display"""

    original_request: str
    modified_request: str
    response: ParsedResponse
    event_info: dict[str, str]
    request_id: str | None = None


@dataclass
class RequestState:
    """Accumulated state for a single request lifecycle.

    Accumulates events from request_info → pre_processing → streaming → chat_completion.

    Invariants:
    - is_complete ⟹ completed_at is not None
    - ¬is_complete ⟹ request is still accumulating events
    """

    request_id: str
    started_at: datetime
    completed_at: datetime | None = None

    # Request data (from request_info or pre_processing events)
    original_request: str = ""
    modified_request: str = ""

    # Response accumulator (from streaming chunks and chat_completion)
    response_chunks: list[str] = field(default_factory=list)
    final_response: str = ""

    # Metadata
    event_info: dict[str, str] = field(default_factory=dict)
    model_id: str = ""
    gateway: str = ""

    # Status
    is_complete: bool = False
    error: str | None = None

    def get_accumulated_response(self) -> str:
        """Get full response text (final or accumulated chunks)."""
        if self.final_response:
            return self.final_response
        return "".join(self.response_chunks)

    def to_display_data(
        self, response_parser: "ResponseParser", event_type: str | None = None
    ) -> DisplayData:
        """Convert accumulated state to DisplayData for view rendering.

        Args:
            response_parser: Parser for response formatting
            event_type: Optional override for event type (e.g., 'streaming_chunk'
                       for late chunks). If None, infers from completion status.
        """
        response_text = self.get_accumulated_response()

        # Debug logging
        from universal_logging import get_logger

        logger = get_logger(__name__)
        logger.debug(
            f"📊 to_display_data: request_id={self.request_id}, "
            f"chunks={len(self.response_chunks)}, "
            f"final={len(self.final_response)}, "
            f"accumulated={len(response_text)} chars, "
            f"event_type={event_type}"
        )

        # Use parser's accumulated response method
        parsed = response_parser.parse_accumulated(response_text, self.is_complete)

        # Determine event type: use provided, or infer from state
        if event_type is None:
            event_type = "chat_completion" if self.is_complete else "in_progress"

        # Ensure event_info has required fields
        event_info = {
            "id": self.request_id,
            "timestamp": self.started_at.isoformat() if self.started_at else "",
            "type": event_type,
            "processing_time": self.event_info.get("processing_time", ""),
            "gateway": self.gateway,
            "model": self.model_id,
        }
        event_info.update(self.event_info)

        return DisplayData(
            original_request=self.original_request,
            modified_request=self.modified_request,
            response=parsed,
            event_info=event_info,
            request_id=self.request_id,
        )
