"""
Compact JSON renderer — single-line NDJSON output.

This is the default renderer. Output is machine-parseable NDJSON.
"""

from __future__ import annotations

import json
from typing import Any


class CompactJSONRenderer:
    """
    Renders canonical records as compact single-line JSON (NDJSON).

    This is the canonical output format. All other renderers must produce
    output that, when ANSI codes are stripped, equals this output.
    """

    def __init__(self, ensure_ascii: bool = False):
        """
        Initialize renderer.

        Args:
            ensure_ascii: If True, escape non-ASCII characters. Default False
                         preserves Unicode for readability.
        """
        self.ensure_ascii = ensure_ascii

    def render(self, record: dict[str, Any]) -> str:
        """
        Render record as compact JSON line.

        Args:
            record: Canonical dict from CanonicalRecordBuilder

        Returns:
            Single-line JSON string (no trailing newline)
        """
        return json.dumps(
            record,
            ensure_ascii=self.ensure_ascii,
            separators=(",", ":"),
            default=str,  # Fallback for non-serializable types
        )
