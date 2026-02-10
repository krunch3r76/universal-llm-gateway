"""
Logging formatter adapter for JSON renderers.

Bridges canonical renderers to Python's logging.Formatter interface.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import Renderer
from .compact import CompactJSONRenderer


class JSONFormatter(logging.Formatter):
    """
    Logging formatter that uses canonical schema and JSON renderers.

    This is the integration point between Python's logging system
    and the structured logging architecture.

    Usage in logging.yaml:
        formatters:
          json:
            class: universal_logging.renderers.JSONFormatter
            # Optional: indent, colors
    """

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        style: str = "%",
        *,
        renderer: Renderer | None = None,
        indent: int | None = None,
        colors: bool = False,
        truncate: bool = False,
        max_field_size: int = 2000,
        **kwargs: Any,
    ):
        """
        Initialize JSON formatter.

        Note: fmt, datefmt, style are accepted to match logging.Formatter's
        interface (required for logging.config.dictConfig compatibility)
        but are ignored since this formatter produces JSON output.

        Args:
            fmt: Format string (ignored, for dictConfig compatibility)
            datefmt: Date format string (ignored, for dictConfig compatibility)
            style: Style character (ignored, for dictConfig compatibility)
            renderer: Custom renderer instance (overrides other options)
            indent: Indentation level for pretty/colorized
            colors: Enable ANSI colors
            truncate: Enable field truncation
            max_field_size: Max size for string fields when truncating
            **kwargs: Additional kwargs (ignored)
        """
        # Pass fmt/datefmt to parent for any edge cases, but we override format()
        super().__init__(fmt=fmt, datefmt=datefmt)

        self.truncate = truncate
        self.max_field_size = max_field_size

        # Create builder once at init, not per format() call
        from ..schema.record_builder import CanonicalRecordBuilder

        self._builder = CanonicalRecordBuilder(
            truncate=self.truncate,
            max_field_size=self.max_field_size,
        )

        if renderer is not None:
            self._renderer = renderer
        elif colors:
            from .colorized import ColorizedJSONRenderer

            self._renderer = ColorizedJSONRenderer(indent=indent)
        elif indent is not None:
            from .pretty import PrettyJSONRenderer

            self._renderer = PrettyJSONRenderer(indent=indent)
        else:
            self._renderer = CompactJSONRenderer()

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as JSON.

        Args:
            record: Python logging LogRecord

        Returns:
            JSON string (single line for compact, multi-line for pretty)
        """
        canonical = self._builder.build(record)
        return self._renderer.render(canonical)
