"""
JSON renderers for canonical log records.

INVARIANT: ∀ renderer, ∀ record: strip_ansi(render(record)) == compact_json(record)

All renderers consume canonical dicts from schema.record_builder.
No renderer may introduce, remove, or reinterpret fields.
"""

from .base import Renderer
from .colorized import ColorizedJSONRenderer
from .compact import CompactJSONRenderer
from .formatter_adapter import JSONFormatter
from .pretty import PrettyJSONRenderer

__all__ = [
    "Renderer",
    "CompactJSONRenderer",
    "PrettyJSONRenderer",
    "ColorizedJSONRenderer",
    "JSONFormatter",
]
