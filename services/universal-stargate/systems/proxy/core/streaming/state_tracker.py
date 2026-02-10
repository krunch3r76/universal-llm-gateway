"""
Stream state tracker for error handling decisions.

Provides a clean abstraction for tracking whether streaming has started,
which determines how errors are handled.
"""


class StreamStateTracker:
    """
    Track streaming state for error handling decisions.

    This class provides a clean abstraction for tracking whether
    streaming has started, which determines how errors are handled.
    """

    def __init__(self):
        """Initialize stream state tracker."""
        self._started = False
        self._first_chunk_sent = False

    def mark_started(self):
        """Mark that streaming has begun (connection established)."""
        self._started = True

    def mark_first_chunk_sent(self):
        """Mark that first chunk was sent (stream definitely active)."""
        self._first_chunk_sent = True

    @property
    def stream_started(self) -> bool:
        """
        Whether streaming has started.

        Returns True if either:
        - Streaming was explicitly marked as started
        - First chunk was sent (implicit start)

        Returns:
            bool: True if streaming has started
        """
        return self._started or self._first_chunk_sent

    def reset(self):
        """Reset state for new request."""
        self._started = False
        self._first_chunk_sent = False
