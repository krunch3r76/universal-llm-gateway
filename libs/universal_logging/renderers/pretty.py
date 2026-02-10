"""
Pretty JSON renderer — indented JSON output for human readability.

Output is still valid JSON, just with whitespace formatting.
"""

from __future__ import annotations

import json
from typing import Any


class PrettyJSONRenderer:
    """
    Renders canonical records as indented JSON.

    Output is valid JSON with indentation for readability.
    Useful for development/debugging when piped to terminal.
    """

    def __init__(self, indent: int = 2, ensure_ascii: bool = False):
        """
        Initialize renderer.

        Args:
            indent: Number of spaces for indentation
            ensure_ascii: If True, escape non-ASCII characters
        """
        self.indent = indent
        self.ensure_ascii = ensure_ascii

    def render(self, record: dict[str, Any]) -> str:
        """
        Render record as indented JSON.

        Args:
            record: Canonical dict from CanonicalRecordBuilder

        Returns:
            Indented JSON string (no trailing newline)
        """
        return json.dumps(
            record,
            indent=self.indent,
            ensure_ascii=self.ensure_ascii,
            default=str,
        )
