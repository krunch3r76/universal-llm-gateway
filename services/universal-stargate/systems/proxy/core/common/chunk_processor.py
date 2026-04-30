"""
Chunk processing for streaming responses.

Handles parsing chunks from the gateway (SSE format and signal/payload format),
extracting content, applying filters, and formatting for SSE output.

Note: Gateway always converts to OpenAI format (delta.content) before sending.
"""

import json
from typing import Any

from sse.core import format_sse
from universal_logging import get_logger

logger = get_logger(__name__)


class ProcessedChunk:
    """
    Result of processing a chunk.

    Attributes:
        is_done: Whether this is a [DONE] marker
        payload: The parsed payload (dict for SSE format, None for [DONE])
        chunk_content: Extracted content string from the chunk
        trimmed_content: Filtered content (if filter was applied)
        should_yield: Whether this chunk should be yielded to client
        sse_format: SSE-formatted bytes ready to yield
        format: Either 'sse' or 'signal_payload'
    """

    def __init__(
        self,
        is_done: bool = False,
        payload: dict[str, Any] | None = None,
        chunk_content: str = "",
        trimmed_content: str | None = None,
        should_yield: bool = True,
        format: str = "sse",
    ):
        self.is_done = is_done
        self.payload = payload
        self.chunk_content = chunk_content
        self.trimmed_content = trimmed_content
        self.should_yield = should_yield
        self.format = format

        # Generate SSE format
        if is_done:
            # Legacy [DONE] (finish_reason chunks handle completion in normal operation)
            self.sse_format = b"data: [DONE]\n\n"
        elif payload:
            if trimmed_content is not None and isinstance(payload, dict):
                # Use trimmed content if available
                trimmed_payload = payload.copy()
                if "choices" in trimmed_payload and trimmed_payload["choices"]:
                    first_choice = trimmed_payload["choices"][0].copy()
                    # OpenAI format - update delta.content
                    if "delta" in first_choice:
                        first_choice["delta"] = first_choice["delta"].copy()
                        first_choice["delta"]["content"] = trimmed_content
                    trimmed_payload["choices"][0] = first_choice
                self.sse_format = format_sse(trimmed_payload).encode("utf-8")
            else:
                self.sse_format = format_sse(payload).encode("utf-8")
        else:
            self.sse_format = b""


class ChunkProcessor:
    """
    Processes streaming chunks from the gateway.

    Responsibilities:
    - Parse chunks (SSE format and signal/payload format)
    - Extract content from parsed chunks
    - Apply content filters if available
    - Format chunks for SSE output
    - Handle [DONE] markers
    """

    def __init__(self, content_filter=None):
        """
        Initialize the chunk processor.

        Args:
            content_filter: Optional content filter to apply to chunks
        """
        self.content_filter = content_filter

    def process_chunk(
        self, raw_chunk: bytes, context: Any | None = None
    ) -> ProcessedChunk | None:
        """
        Process a raw chunk from the gateway.

        Args:
            raw_chunk: Raw bytes chunk from gateway
            context: Optional context (for logging)

        Returns:
            ProcessedChunk object, or None if chunk couldn't be parsed
        """
        try:
            # SSE format requires text (decode only this branch)
            if raw_chunk.startswith(b"data: "):
                chunk_str = raw_chunk.decode("utf-8")
                return self._process_sse_format(chunk_str, context)

            # NDJSON: json.loads accepts bytes — no decode needed
            try:
                event = json.loads(raw_chunk)

                # Skip special metadata chunks (stream_id, request_id, etc.)
                if event.get("_type") in ("stream_id", "request_id", "metadata"):
                    logger.info("Skipping metadata: _type=%s", event.get("_type"))
                    return None

                return self._process_signal_payload_format(event, context)
            except json.JSONDecodeError:
                logger.warning(
                    "JSON PARSE ERROR: Failed to parse chunk: %s",
                    repr(raw_chunk[:100]),
                )
                return None

        except UnicodeDecodeError as e:
            # Only reachable from the SSE decode branch
            logger.warning("Failed to decode chunk: %s", e)
            return None
        except Exception as e:
            logger.warning("ERROR PARSING STREAMING EVENT: %s", e)
            logger.warning("CHUNK CONTENT: %s", repr(raw_chunk[:500]))
            return None

    def _process_sse_format(
        self, chunk_str: str, context: Any | None = None
    ) -> ProcessedChunk | None:
        """
        Process SSE format chunk: "data: {...}" or "data: [DONE]"

        Args:
            chunk_str: Chunk string with "data: " prefix
            context: Optional context

        Returns:
            ProcessedChunk object
        """
        # Extract JSON after "data: "
        json_str = chunk_str[6:].strip()
        logger.debug("EXTRACTED JSON FROM SSE: %s", json_str[:200])

        # Check for [DONE] (finish_reason chunks handle completion in normal operation)
        if json_str == "[DONE]":
            return ProcessedChunk(is_done=True, should_yield=True)

        # Parse the JSON payload
        try:
            payload = json.loads(json_str)

            # Extract content for filtering
            chunk_content = ""
            if payload and isinstance(payload, dict):
                # Standard OpenAI delta format (gateway converts to this format)
                if "choices" in payload and payload["choices"]:
                    first_choice = payload["choices"][0]
                    # OpenAI format: {"delta": {"content": "..."}}
                    if "delta" in first_choice:
                        chunk_content = first_choice.get("delta", {}).get("content", "")

            # Check if this chunk has finish_reason (indicates completion)
            has_finish_reason = False
            if payload and isinstance(payload, dict):
                # Check top-level finish_reason
                if payload.get("finish_reason"):
                    has_finish_reason = True
                # Check choices[].finish_reason
                elif "choices" in payload:
                    for choice in payload["choices"]:
                        if choice.get("finish_reason"):
                            has_finish_reason = True
                            break

            # Process through filter if available
            trimmed_content = None
            if self.content_filter and chunk_content:
                trimmed_content = self.content_filter.process_chunk(chunk_content)

            # Handle completion chunks with finish_reason - always forward as-is
            if has_finish_reason:
                should_yield = True
                logger.info(
                    "Forwarding completion chunk with finish_reason (bypassing filter)"
                )

                # If filter buffered content never forwarded, flush it first
                if self.content_filter and hasattr(
                    self.content_filter, "force_flush_on_completion"
                ):
                    flushed_content = self.content_filter.force_flush_on_completion()
                    if flushed_content:
                        # Yield flushed content before completion chunk
                        trimmed_content = flushed_content
            else:
                # Regular chunk - respect filter decision
                should_yield = (
                    not self.content_filter
                    or self.content_filter.should_forward_chunk()
                )

            return ProcessedChunk(
                payload=payload,
                chunk_content=chunk_content,
                trimmed_content=trimmed_content,
                should_yield=should_yield,
                format="sse",
            )

        except json.JSONDecodeError as e:
            logger.warning("Failed to parse SSE JSON: %s", e)
            return None

    def _process_signal_payload_format(
        self, event: dict[str, Any], context: Any | None = None
    ) -> ProcessedChunk | None:
        """
        Process signal/payload format chunk: {"signal": "...", "payload": {...}}

        Args:
            event: Parsed event dictionary
            context: Optional context

        Returns:
            ProcessedChunk object
        """
        signal = event.get("signal")
        payload = event.get("payload")

        # Log errors with full context
        if signal == "error":
            request_id = (
                getattr(context, "request_id", "unknown") if context else "unknown"
            )
            logger.error(
                "Gateway streaming error for request %s: %s", request_id, payload
            )

        # Handle [DONE] (finish_reason chunks handle completion in normal operation)
        if payload == "[DONE]":
            return ProcessedChunk(
                is_done=True, format="signal_payload", should_yield=True
            )

        # Extract content for filtering
        chunk_content = ""
        if payload and isinstance(payload, dict):
            # Standard OpenAI delta format (gateway converts to this format)
            if "choices" in payload and payload["choices"]:
                first_choice = payload["choices"][0]
                # OpenAI format: {"delta": {"content": "..."}}
                if "delta" in first_choice:
                    chunk_content = first_choice.get("delta", {}).get("content", "")

        # Check if this chunk has finish_reason (indicates completion)
        has_finish_reason = False
        if payload and isinstance(payload, dict):
            # Check top-level finish_reason
            if payload.get("finish_reason"):
                has_finish_reason = True
            # Check choices[].finish_reason
            elif "choices" in payload:
                for choice in payload["choices"]:
                    if choice.get("finish_reason"):
                        has_finish_reason = True
                        break

        # Process through filter if available
        trimmed_content = None
        if self.content_filter and chunk_content:
            trimmed_content = self.content_filter.process_chunk(chunk_content)

        # Handle completion chunks with finish_reason - always forward as-is
        if has_finish_reason:
            should_yield = True
            logger.info(
                "Forwarding completion chunk with finish_reason (bypassing filter)"
            )

            # If filter has buffered content that was never forwarded, flush it first
            if self.content_filter and hasattr(
                self.content_filter, "force_flush_on_completion"
            ):
                flushed_content = self.content_filter.force_flush_on_completion()
                if flushed_content:
                    # Yield flushed content before completion chunk
                    trimmed_content = flushed_content
        else:
            # Regular chunk - respect filter decision
            should_yield = (
                not self.content_filter or self.content_filter.should_forward_chunk()
            )

        return ProcessedChunk(
            payload=payload,
            chunk_content=chunk_content,
            trimmed_content=trimmed_content,
            should_yield=should_yield,
            format="signal_payload",
        )

    def clear_filter_pending(self):
        """
        Clear any pending state in the content filter.

        Should be called after a trimmed chunk is sent.
        """
        if self.content_filter:
            self.content_filter.clear_pending()
