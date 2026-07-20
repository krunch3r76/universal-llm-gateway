"""Strict client stream-flag predicate.

Single definition of "did the client request a streaming response": only a
JSON boolean ``true`` enables streaming. A JSON string ``"stream": "false"``
is truthy under ``bool()`` / ``.get("stream", False)``, so naive reads
silently stream when the client asked not to. This predicate is the strict
guard used at every independent request-body parse point that cannot reach
the single proxy-ingress coercion boundary (federation relay/direct paths,
native-provider passthrough).
"""

from collections.abc import Mapping
from typing import Any


def client_requested_stream(body: Mapping[str, Any]) -> bool:
    """True iff ``body["stream"]`` is the JSON boolean ``true`` (a strict ``is True`` identity check, not merely a truthy value), so callers cannot accidentally enable streaming with ``"stream": 1`` or similar."""
    return body.get("stream") is True
