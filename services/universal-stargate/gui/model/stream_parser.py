"""
Stream parser for handling streaming response data.

Specialized parser for Server-Sent Events (SSE) and streaming responses
from the universal_stargate monitoring system.
"""

import json
from typing import Any

from universal_logging import get_logger

from .data_structures import ParsedResponse

logger = get_logger(__name__)


class StreamParser:
    """Specialized parser for streaming response data"""

    def parse(self, response_data: dict[str, Any]) -> ParsedResponse:
        """
        Parse streaming response data.

        Args:
            response_data: Raw streaming response data

        Returns:
            ParsedResponse object for streaming data
        """
        try:
            # Handle individual chunk updates
            if response_data.get("type") == "streaming_chunk":
                chunk = response_data.get("chunk", "")
                chunk_number = response_data.get("chunk_number", 0)

                # Parse the chunk content
                parsed_chunk = self._parse_single_chunk(chunk)

                logger.debug(
                    f"Parsed streaming chunk {chunk_number}, length: {len(parsed_chunk)}"
                )

                return ParsedResponse(
                    raw_data=response_data,
                    formatted_text=f"Chunk {chunk_number}: {parsed_chunk}",
                    response_type="streaming_chunk",
                    is_streaming=True,
                    chunks=[parsed_chunk] if parsed_chunk else [],
                )
            elif response_data.get("type") == "streaming_chunk_batch":
                # Handle batched chunk updates (more efficient)
                content = response_data.get("content", "")
                start_chunk_number = response_data.get("start_chunk_number", 0)
                chunk_count = response_data.get("chunk_count", 0)

                # Parse the combined content
                parsed_chunk = self._parse_single_chunk(content)

                # Only log minimal info about the last chunk in the batch
                logger.debug(
                    f"Parsed chunk batch {start_chunk_number}-{start_chunk_number + chunk_count - 1}, length: {len(parsed_chunk)}"
                )

                return ParsedResponse(
                    raw_data=response_data,
                    formatted_text=f"Batch {start_chunk_number}-{start_chunk_number + chunk_count - 1}: {parsed_chunk}",
                    response_type="streaming_chunk_batch",
                    is_streaming=True,
                    chunks=[parsed_chunk] if parsed_chunk else [],
                )
            elif "captured_chunks" in response_data:
                # Parse SSE chunks into readable format
                chunks = response_data["captured_chunks"]
                parsed_chunks = self._parse_sse_chunks(chunks)
                formatted_display = self._format_streaming_display(parsed_chunks)

                return ParsedResponse(
                    raw_data=response_data,
                    formatted_text=formatted_display,
                    response_type="streaming",
                    is_streaming=True,
                    chunks=parsed_chunks,
                )
            else:
                # No captured chunks - show placeholder
                return ParsedResponse(
                    raw_data=response_data,
                    formatted_text=self._create_streaming_placeholder(response_data),
                    response_type="streaming_placeholder",
                    is_streaming=True,
                    chunks=[],
                )

        except Exception as e:
            logger.error(f"Error parsing streaming response: {e}")
            return ParsedResponse(
                raw_data=response_data,
                formatted_text=f"Error parsing streaming response: {e}\n\nRaw: {str(response_data)[:500]}...",
                response_type="error",
                is_streaming=True,
                error_message=str(e),
            )

    def _parse_sse_chunks(self, sse_chunks: list[str]) -> list[str]:
        """
        Parse Server-Sent Event chunks.

        Args:
            sse_chunks: List of raw SSE chunk strings

        Returns:
            List of parsed chunk contents
        """
        parsed_chunks = []

        for chunk in sse_chunks:
            try:
                # SSE chunks typically start with "data: "
                if chunk.startswith("data: "):
                    data_part = chunk[6:]  # Remove "data: " prefix

                    if data_part.strip() == "[DONE]":
                        parsed_chunks.append("[DONE]")
                    else:
                        # Try to parse as JSON
                        try:
                            chunk_data = json.loads(data_part)
                            # Extract content if it's a chat completion chunk
                            if "choices" in chunk_data and chunk_data["choices"]:
                                choice = chunk_data["choices"][0]
                                if "delta" in choice and "content" in choice["delta"]:
                                    content = choice["delta"]["content"]
                                    # Only add content if it's not empty
                                    if content:
                                        parsed_chunks.append(content)
                                else:
                                    # Delta without content - check if it's a finish chunk
                                    if choice.get("finish_reason"):
                                        # This is a finish chunk, don't add anything
                                        pass
                                    else:
                                        # Other delta without content - skip silently
                                        pass
                            else:
                                # Add the whole chunk if we can't extract content
                                parsed_chunks.append(json.dumps(chunk_data, indent=2))
                        except json.JSONDecodeError:
                            # Not JSON, add as-is
                            parsed_chunks.append(data_part)
                else:
                    # Not an SSE chunk, add as-is
                    parsed_chunks.append(chunk)

            except Exception as e:
                logger.warning(f"Error parsing SSE chunk: {e}")
                parsed_chunks.append(f"[Error parsing chunk: {e}]")

        return parsed_chunks

    def _parse_single_chunk(self, chunk: str) -> str:
        """
        Parse a single SSE chunk.

        Args:
            chunk: Raw chunk string

        Returns:
            Parsed chunk content
        """
        try:
            # SSE chunks typically start with "data: "
            if chunk.startswith("data: "):
                data_part = chunk[6:]  # Remove "data: " prefix

                if data_part.strip() == "[DONE]":
                    return "[DONE]"
                else:
                    # Try to parse as JSON
                    try:
                        chunk_data = json.loads(data_part)
                        # Extract content if it's a chat completion chunk
                        if "choices" in chunk_data and chunk_data["choices"]:
                            choice = chunk_data["choices"][0]
                            if "delta" in choice and "content" in choice["delta"]:
                                content = choice["delta"]["content"]
                                # Only return content if it's not empty
                                return content if content else ""
                            else:
                                # Delta without content - check if it's a finish chunk
                                if choice.get("finish_reason"):
                                    # This is a finish chunk, return empty string
                                    return ""
                                else:
                                    # Other delta without content - return empty string
                                    return ""
                        else:
                            # Add the whole chunk if we can't extract content
                            return json.dumps(chunk_data, indent=2)
                    except json.JSONDecodeError:
                        # Not JSON, add as-is
                        return data_part
            else:
                # Not an SSE chunk, add as-is
                return chunk

        except Exception as e:
            logger.warning(f"Error parsing single chunk: {e}")
            return f"[Error parsing chunk: {e}]"

    def _format_streaming_display(self, parsed_chunks: list[str]) -> str:
        """
        Format parsed chunks for display.

        Args:
            parsed_chunks: List of parsed chunk contents

        Returns:
            Formatted string for display
        """
        if not parsed_chunks:
            return "Streaming Response\n\nNo content chunks captured"

        # Join content chunks to reconstruct the response
        content_chunks = [chunk for chunk in parsed_chunks if chunk != "[DONE]"]

        if content_chunks:
            full_content = "".join(content_chunks)

            return f"Streaming Response\n\nContent:\n{full_content}\n\nChunks received: {len(content_chunks)}"
        else:
            return f"Streaming Response\n\nReceived {len(parsed_chunks)} chunks but no content extracted"

    def _create_streaming_placeholder(self, response_data: dict[str, Any]) -> str:
        """
        Create placeholder text for streaming responses without captured chunks.

        Args:
            response_data: Raw response data

        Returns:
            Placeholder text
        """
        media_type = response_data.get("media_type", "unknown")

        placeholder = "Streaming Response\n\n"
        placeholder += f"Media Type: {media_type}\n"
        placeholder += "Content delivered via Server-Sent Events (SSE)\n\n"

        if response_data.get("note"):
            placeholder += f"Note: {response_data['note']}\n"

        placeholder += "\nResponse content was streamed directly to client."

        return placeholder
