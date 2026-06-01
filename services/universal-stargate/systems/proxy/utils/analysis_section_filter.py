"""
Analysis section filter for GPT-OSS models.

GPT-OSS models emit an analysis section that ends with "<|channel|>final<|message|>"
before the actual response. This filter removes everything before the pattern
to prevent clients from seeing the analysis.
"""

from universal_logging import get_logger

logger = get_logger(__name__)


class AnalysisSectionFilter:
    """
    Filters out analysis sections from GPT-OSS model responses.

    Accumulates content until the pattern "final" + "<|message|>" is detected,
    then returns only the content after that marker.
    """

    def __init__(self, request_id: str | None = None):
        """
        Initialize the filter.

        Args:
            request_id: Optional request ID for logging
        """
        self.request_id = request_id
        self.content_buffer = ""
        self.passed_analysis_section = False
        self.pending_trimmed_content = None

    def process_chunk(self, chunk_content: str) -> str | None:
        """
        Process a chunk and return trimmed content if pattern is detected.

        Args:
            chunk_content: The content from the current chunk

        Returns:
            None if still buffering, trimmed content (possibly empty) if pattern
                detected
        """
        if not chunk_content:
            return None

        # Accumulate content
        self.content_buffer += chunk_content

        # Check if we've seen the pattern
        if not self.passed_analysis_section:
            if self._detect_pattern():
                self.passed_analysis_section = True
                logger.info(
                    f"Analysis section detected and filtered out for"
                    f"request {self.request_id}"
                )
                logger.debug(
                    f"Buffer content (last 200 chars): ...{self.content_buffer[-200:]}"
                )

                # Extract content after the marker
                trimmed_content = self._extract_content_after_marker()
                if trimmed_content is not None:
                    # trimmed_content can be empty string if marker is at end
                    self.pending_trimmed_content = trimmed_content

                    # Truncate buffer to only contain content after the marker
                    self.content_buffer = trimmed_content
                    logger.debug(
                        f"Buffer truncated to content after marker (length:"
                        f"{len(self.content_buffer)})"
                    )

                    logger.info(
                        f"Returning trimmed content (first 100 chars):"
                        f"'{trimmed_content[:100]}...'"
                    )
                    return trimmed_content

        return None

    def _detect_pattern(self) -> bool:
        """
        Detect if we've seen "final" followed by "<|message|>".

        Returns:
            True if pattern detected, False otherwise
        """
        if (
            "final" not in self.content_buffer
            or "<|message|>" not in self.content_buffer
        ):
            return False

        # Check if they appear in order
        final_pos = self.content_buffer.rfind("final")
        message_pos = self.content_buffer.rfind("<|message|>")

        return final_pos < message_pos

    def _extract_content_after_marker(self) -> str | None:
        """
        Extract content after the "<|message|>" marker.

        Returns:
            Content after the marker, empty string if marker found but no content,
            or None if marker not found
        """
        message_pos = self.content_buffer.rfind("<|message|>")
        if message_pos == -1:
            logger.debug("Marker <|message|> not found in buffer")
            return None

        message_end_pos = message_pos + len("<|message|>")
        content_after_marker = self.content_buffer[message_end_pos:]

        logger.debug(
            f"Extracted content after marker: '{content_after_marker[:100]}...'"
            f"(length: {len(content_after_marker)})"
        )

        # Return empty string if marker found but no content after it
        # This signals to skip the chunk entirely
        return content_after_marker

    def should_forward_chunk(self) -> bool:
        """
        Check if chunks should be forwarded to the client.

        Returns:
            True if we've passed the analysis section, False otherwise
        """
        return self.passed_analysis_section

    def get_pending_trimmed_content(self) -> str | None:
        """
        Get the pending trimmed content (first chunk after marker).

        Returns:
            Trimmed content or None
        """
        return self.pending_trimmed_content

    def clear_pending(self):
        """Clear the pending trimmed content."""
        self.pending_trimmed_content = None

    def reset(self):
        """Reset the filter state."""
        self.content_buffer = ""
        self.passed_analysis_section = False
        self.pending_trimmed_content = None

    def filter_content(self, content: str) -> str:
        """
        Filter a complete content string (for non-streaming responses).

        Args:
            content: The complete content string to filter

        Returns:
            Filtered content string
        """
        if not content:
            return content

        # Reset the filter state
        self.reset()

        # Process the entire content
        filtered = self.process_chunk(content)

        # If pattern was detected, return filtered content
        if filtered is not None:
            logger.debug(
                f"Non-streaming content filtered: original length"
                f"{len(content)}, filtered length {len(filtered)}"
            )
            return filtered

        # If pattern not detected, return original content
        logger.debug("Non-streaming content not filtered (no pattern detected)")
        return content

    def force_flush_on_completion(self) -> str | None:
        """
        Force flush buffered content when completion is detected.

        This is called when a finish_reason chunk is received but the analysis
        pattern was never detected (e.g., due to token limits).

        Returns:
            Buffered content that should be sent to client, or None if empty
        """
        if not self.passed_analysis_section and self.content_buffer:
            logger.warning(
                f"Forcing flush of buffered content on completion for"
                f"request {self.request_id} f"
                f"({len(self.content_buffer)} chars)"
            )

            # Mark as passed to prevent further buffering
            self.passed_analysis_section = True

            # Return the buffered content to be flushed
            flushed_content = self.content_buffer
            return flushed_content if flushed_content else None

        return None


def create_content_filter(
    model_name: str | None = None, request_id: str | None = None
) -> AnalysisSectionFilter | None:
    """
    Factory function to create the appropriate content filter based on model.

    Args:
        model_name: The model name/identifier
        request_id: Optional request ID for logging

    Returns:
        Content filter instance or None if no filter needed
    """
    if not model_name:
        return None

    model_lower = model_name.lower()

    # Re-enabled for OSS models
    logger.debug(
        f"Checking if analysis section filtering needed for model: {model_name}"
    )

    # Check if this is a GPT-OSS model
    # Check for various patterns that might indicate GPT-OSS models
    gpt_oss_patterns = ["gpt-oss", "gptoss", "oss-gpt", "ossgpt", "gpt_oss", "oss_gpt"]

    # Check if any pattern matches
    if any(pattern in model_lower for pattern in gpt_oss_patterns):
        logger.info(f"Creating analysis section filter for GPT-OSS model: {model_name}")
        return AnalysisSectionFilter(request_id=request_id)

    logger.debug(f"No filter created for model: {model_name}")
    return None
