"""
Renderer protocol for canonical log records.

All renderers must implement this protocol.
"""

from typing import Any, Protocol


class Renderer(Protocol):
    """
    Protocol for log record renderers.

    Renderers convert canonical dicts to string output.
    The output must be valid JSON (possibly with ANSI codes that can be stripped).
    """

    def render(self, record: dict[str, Any]) -> str:
        """
        Render canonical record to string.

        Args:
            record: Canonical dict from CanonicalRecordBuilder

        Returns:
            String representation (NDJSON line, no trailing newline)
        """
        ...
