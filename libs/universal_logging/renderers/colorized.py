"""
Colorized JSON renderer — JSON with ANSI syntax highlighting.

Output is valid JSON with ANSI escape codes for terminal display.
When ANSI codes are stripped, output equals CompactJSONRenderer output.

INVARIANT: strip_ansi(colorized_output) == compact_output
"""

from __future__ import annotations

import json
import re
from typing import Any


class ColorizedJSONRenderer:
    """
    Renders canonical records as JSON with ANSI color codes.

    Colors are applied to JSON syntax elements:
    - Keys: cyan
    - Strings: green
    - Numbers: yellow
    - Booleans/null: magenta
    - Level-based message highlighting

    When ANSI codes are stripped, output is valid compact JSON.
    """

    # ANSI color codes
    COLORS = {
        "key": "\033[36m",  # Cyan
        "string": "\033[32m",  # Green
        "number": "\033[33m",  # Yellow
        "bool": "\033[35m",  # Magenta
        "null": "\033[35m",  # Magenta
        "reset": "\033[0m",
        # Level-specific colors
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[91m",  # Bright red
    }

    def __init__(self, indent: int | None = None, ensure_ascii: bool = False):
        """
        Initialize renderer.

        Args:
            indent: Indentation level (None for compact)
            ensure_ascii: If True, escape non-ASCII characters
        """
        self.indent = indent
        self.ensure_ascii = ensure_ascii

    def render(self, record: dict[str, Any]) -> str:
        """
        Render record as colorized JSON.

        Args:
            record: Canonical dict from CanonicalRecordBuilder

        Returns:
            JSON string with ANSI color codes
        """
        # First, render as plain JSON
        if self.indent is not None:
            json_str = json.dumps(
                record,
                indent=self.indent,
                ensure_ascii=self.ensure_ascii,
                default=str,
            )
        else:
            json_str = json.dumps(
                record,
                ensure_ascii=self.ensure_ascii,
                separators=(",", ":"),
                default=str,
            )

        # Apply syntax highlighting
        return self._colorize(json_str, record.get("level", "INFO"))

    def _colorize(self, json_str: str, level: str) -> str:
        """Apply ANSI color codes to JSON string."""
        reset = self.COLORS["reset"]

        # Color keys (quoted strings followed by colon)
        # Preserve whitespace after key
        json_str = re.sub(
            r'"([^"]+)"(\s*):',
            lambda m: f'{self.COLORS["key"]}"{m.group(1)}"{reset}{m.group(2)}:',
            json_str,
        )

        # Color string values (quoted strings after colon)
        # Preserve exact whitespace pattern
        json_str = re.sub(
            r':(\s*)"([^"]*)"',
            lambda m: f':{m.group(1)}{self.COLORS["string"]}"{m.group(2)}"{reset}',
            json_str,
        )

        # Color numbers (preserve whitespace)
        # Use negative lookahead/lookbehind to avoid matching within quoted strings
        json_str = re.sub(
            r":(\s*)(-?\d+\.?\d*)(?=\s*[,}\]])",
            lambda m: f":{m.group(1)}{self.COLORS['number']}{m.group(2)}{reset}",
            json_str,
        )

        # Color booleans and null (preserve whitespace)
        # Process after strings/numbers to avoid conflicts
        # Simple pattern: just match the keyword after colon+whitespace
        # The JSON structure ensures these won't conflict with strings/numbers
        for keyword in ["true", "false", "null"]:
            escaped_keyword = re.escape(keyword)
            # Match colon, optional whitespace, then the keyword
            pattern = r":(\s*)" + escaped_keyword
            json_str = re.sub(
                pattern,
                lambda m, kw=keyword: f":{m.group(1)}{self.COLORS['bool']}{kw}{reset}",
                json_str,
            )

        # Highlight level value based on severity
        # This needs to happen after string coloring to override it
        level_color = self.COLORS.get(level, "")
        if level_color:
            # Match the already-colored level field
            # Escape ANSI codes to avoid regex character class issues
            escaped_reset = re.escape(reset)
            escaped_string_color = re.escape(self.COLORS["string"])
            pattern = (
                re.escape('"level"')
                + escaped_reset
                + r":(\s*)"
                + escaped_string_color
                + re.escape(f'"{level}"')
            )
            json_str = re.sub(
                pattern,
                lambda m: f'"level"{reset}:{m.group(1)}{level_color}"{level}"{reset}',
                json_str,
            )

        return json_str


def strip_ansi(text: str) -> str:
    """
    Remove ANSI escape codes from text.

    This function is used to verify the invariant:
    strip_ansi(colorized_output) == compact_output

    Args:
        text: String potentially containing ANSI codes

    Returns:
        String with all ANSI escape sequences removed
    """
    ansi_pattern = re.compile(r"\033\[[0-9;]*m")
    return ansi_pattern.sub("", text)
