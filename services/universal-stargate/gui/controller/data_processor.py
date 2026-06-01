"""
Data processor for converting between data formats.

Handles transformation of raw monitoring data into structured events
and display-ready formats.
"""

import json
from datetime import datetime
from typing import Any

from universal_logging import get_logger

from ..model.data_structures import DisplayData, ParsedResponse, StargateEvent

logger = get_logger(__name__)


class DataProcessor:
    """Processes and transforms data between formats"""

    def create_event(self, raw_data: dict[str, Any]) -> StargateEvent:
        """
        Convert raw JSON to StargateEvent.

        Args:
            raw_data: Raw monitoring data from network receiver

        Returns:
            StargateEvent object
        """
        # Process converted MonitoringEvent data (already a dict from event_controller)
        event_data = raw_data

        # Extract response data based on event type
        if raw_data.get("type") == "streaming_chunk":
            # Debug logging
            logger.debug(
                f"Processing streaming_chunk event: "
                f"chunk='{event_data.get('chunk', '')}', "
                f"chunk_number={event_data.get('chunk_number', 0)}"
            )

            # Create response_data structure for streaming chunks
            response_data = {
                "type": "streaming_chunk",
                "chunk": event_data.get("chunk", ""),
                "chunk_number": event_data.get("chunk_number", 0),
                "request_id": event_data.get("request_id", raw_data.get("request_id")),
            }
        elif raw_data.get("type") == "streaming_chunk_batch":
            # Handle batched chunks (more efficient)
            logger.debug(
                f"Processing streaming_chunk_batch event:"
                f"{event_data.get('chunk_count', 0)} chunks"
            )
            response_data = {
                "type": "streaming_chunk_batch",
                "content": event_data.get("content", ""),
                "start_chunk_number": event_data.get("start_chunk_number", 0),
                "chunk_count": event_data.get("chunk_count", 0),
                "request_id": event_data.get("request_id", raw_data.get("request_id")),
            }
        else:
            # For chat_completion and other events, extract from event_data
            response_data = event_data.get("response")
            preview = str(response_data)[:100] if response_data else "None"
            logger.debug(
                f"Extracted response_data for {raw_data.get('type')} event: "
                f"{type(response_data)} - {preview}"
            )

        # For streaming chunks, don't try to extract request data since it's not
        # included
        if (
            raw_data.get("type") == "streaming_chunk"
            or raw_data.get("type") == "streaming_chunk_batch"
        ):
            # Streaming chunks don't contain request data - use empty dicts
            # This ensures streaming chunks never clear the request panels
            original_request = {}
            modified_request = {}
        else:
            # Non-streaming events contain request data
            # Handle None values - convert to empty dict for consistent processing
            original_request = event_data.get("original_request") or {}
            modified_request = event_data.get("modified_request") or {}

        return StargateEvent(
            id=event_data.get("id", raw_data.get("id", "unknown")),
            timestamp=self._parse_timestamp(raw_data.get("timestamp")),
            event_type=event_data.get(
                "type", raw_data.get("type", "unknown")
            ),  # Use type from data section if available
            original_request=original_request,
            modified_request=modified_request,
            stargate_actions=event_data.get("stargate_actions", []),
            processing_time_ms=event_data.get("processing_time_ms", 0.0),
            gateway_endpoint=event_data.get("gateway_endpoint", ""),
            token_metrics=event_data.get("token_metrics"),
            model_metadata=event_data.get("model_metadata"),
            response_data=response_data,
            request_id=event_data.get("request_id", raw_data.get("request_id")),
        )

    def create_display_data(
        self, event: StargateEvent, parsed_response: ParsedResponse
    ) -> DisplayData:
        """
        Create display-ready data from event and parsed response.

        Args:
            event: StargateEvent object
            parsed_response: ParsedResponse object

        Returns:
            DisplayData object ready for view
        """
        # Extract request_id from event (now available at top level)
        request_id = event.request_id
        logger.debug(f"Extracted request_id from event: {request_id}")

        # Format the request data
        formatted_original = self._format_json(event.original_request)
        formatted_modified = self._format_json(event.modified_request)

        return DisplayData(
            original_request=formatted_original,
            modified_request=formatted_modified,
            response=parsed_response,
            event_info=self._create_event_info(event),
            request_id=request_id,
        )

    def _format_json(self, data: dict[str, Any]) -> str:
        """
        Format JSON for display.

        Args:
            data: Dictionary to format

        Returns:
            Formatted JSON string
        """
        try:
            if not data:
                return "No data"
            return json.dumps(data, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error formatting JSON: {e}")
            return f"Error formatting JSON: {e}\n\nRaw: {str(data)[:500]}..."

    def _create_event_info(self, event: StargateEvent) -> dict[str, str]:
        """
        Create event information for display.

        Args:
            event: StargateEvent object

        Returns:
            Dictionary of display-ready event info
        """
        return {
            "id": event.id,
            "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "type": event.event_type,
            "processing_time": f"{event.processing_time_ms:.2f} ms",
            "gateway": event.gateway_endpoint,
            "actions": self._format_actions(event.stargate_actions),
        }

    def _format_actions(self, actions: list) -> str:
        """
        Format stargate actions for display.

        Args:
            actions: List of action strings

        Returns:
            Formatted actions string
        """
        if not actions:
            return "None"

        actions_text = ", ".join(actions)
        # Truncate if too long
        if len(actions_text) > 80:
            actions_text = actions_text[:77] + "..."

        return actions_text

    def _parse_timestamp(self, timestamp_str) -> datetime:
        """
        Parse timestamp value into datetime object.

        Args:
            timestamp_str: Timestamp as string (ISO format) or float (Unix timestamp)

        Returns:
            datetime object
        """
        try:
            if timestamp_str:
                # Handle float timestamp (Unix timestamp)
                if isinstance(timestamp_str, (int, float)):
                    return datetime.fromtimestamp(timestamp_str)
                # Handle string timestamp (ISO format)
                elif isinstance(timestamp_str, str):
                    # Handle ISO format with Z suffix
                    if timestamp_str.endswith("Z"):
                        timestamp_str = timestamp_str[:-1] + "+00:00"
                    return datetime.fromisoformat(timestamp_str)
            return datetime.now()
        except Exception as e:
            logger.warning(f"Failed to parse timestamp '{timestamp_str}': {e}")
            return datetime.now()
