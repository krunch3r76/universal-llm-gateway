"""
Response parser for universal_stargate monitoring data.

Handles parsing of different response types including JSON and streaming responses.
"""

import json
from typing import Any

from universal_logging import get_logger

from .data_structures import ParsedResponse
from .stream_parser import StreamParser

logger = get_logger(__name__)


class ResponseParser:
    """Parses response data into display-ready format"""

    def __init__(self):
        self.stream_parser = StreamParser()

    def parse(self, response_data: dict[str, Any] | None) -> ParsedResponse:
        """
        Parse response data based on type.

        Args:
            response_data: Raw response data from monitoring event

        Returns:
            ParsedResponse object with formatted display data
        """
        if not response_data:
            return ParsedResponse(
                raw_data={},
                formatted_text="No response data available",
                response_type="empty",
                is_streaming=False,
            )

        # Check if streaming response
        if self._is_streaming_response(response_data):
            return self.stream_parser.parse(response_data)
        else:
            return self._parse_json_response(response_data)

    def _is_streaming_response(self, data: dict[str, Any]) -> bool:
        """
        Detect if this is a streaming response.

        Args:
            data: Response data to check

        Returns:
            True if this appears to be a streaming response
        """
        return (
            data.get("stream", False)
            or data.get("media_type") == "text/event-stream"
            or "captured_chunks" in data
            or data.get("type") == "streaming_response"
            or data.get("type") == "streaming_chunk"
        )

    def _parse_json_response(self, data: dict[str, Any]) -> ParsedResponse:
        """
        Parse standard JSON response.

        Args:
            data: JSON response data

        Returns:
            ParsedResponse for JSON data
        """
        try:
            # Handle pre-processing events (no response yet)
            if data is None:
                return ParsedResponse(
                    raw_data={},
                    formatted_text="⏳ Request being processed...\n\nWaiting"
                    "for gateway response...",
                    response_type="pre_processing",
                    is_streaming=False,
                )

            # Enhanced formatting for model responses with new schema fields
            formatted_data = self._format_model_response(data)
            formatted = json.dumps(formatted_data, indent=2, ensure_ascii=False)

            return ParsedResponse(
                raw_data=data,
                formatted_text=formatted,
                response_type="json",
                is_streaming=False,
            )
        except Exception as e:
            logger.error(f"Error formatting JSON response: {e}")
            return ParsedResponse(
                raw_data=data,
                formatted_text=f"Error formatting JSON: {e}\n\nRaw:"
                f"{str(data)[:1000]}...f",
                response_type="error",
                is_streaming=False,
                error_message=str(e),
            )

    def parse_accumulated(
        self, response_text: str, is_complete: bool
    ) -> ParsedResponse:
        """Parse accumulated response text from RequestState.

        Args:
            response_text: Accumulated response text (from chunks or final)
            is_complete: Whether the request has completed

        Returns:
            ParsedResponse for the accumulated content
        """
        if not response_text:
            return ParsedResponse(
                raw_data={},
                formatted_text="⏳ Waiting for response..." if not is_complete else "",
                response_type="empty",
                is_streaming=not is_complete,
            )

        return ParsedResponse(
            raw_data={"content": response_text},
            formatted_text=response_text,
            response_type="complete" if is_complete else "streaming",
            is_streaming=not is_complete,
            chunks=None,  # No chunk navigation in Phase 2
        )

    def _format_model_response(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Format model response data for display with new gateway schema.

        Args:
            data: Raw response data

        Returns:
            Formatted data optimized for new schema
        """
        try:
            # Handle models list response
            if (
                isinstance(data, dict)
                and data.get("object") == "list"
                and "data" in data
            ):
                models = data.get("data", [])

                # Add schema summary
                return {
                    **data,
                    "_schema_summary": {
                        "total_models": len(models),
                        "active_models": len(
                            [m for m in models if m.get("status") == "active"]
                        ),
                        "formats": list(
                            set(m.get("format") for m in models if m.get("format"))
                        ),
                        "input_schemas": list(
                            set(
                                m.get("input_schema")
                                for m in models
                                if m.get("input_schema")
                            )
                        ),
                    },
                }

            # Handle individual model response
            elif isinstance(data, dict) and "id" in data:
                # Add model summary for display
                return {
                    **data,
                    "_model_summary": {
                        "status": data.get("status", "unknown"),
                        "version": data.get("version", "unknown"),
                        "capabilities": list(data.get("capabilities", {}).keys()),
                        "tags": data.get("tags", []),
                    },
                }

            return data

        except Exception as e:
            logger.warning(f"Error formatting model response: {e}")
            return data
