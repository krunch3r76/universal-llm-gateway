"""Derive canonical conversation id from a product chat URL."""

from __future__ import annotations

import re

_CHATGPT_CONV = re.compile(
    r"chatgpt\.com/(?:g/[^/]+/)?c/([^/?#]+)",
    re.I,
)


def conversation_id_from_url(url: str) -> str | None:
    """Return the path segment after ``/c/`` when *url* looks like a chat thread."""
    if not url:
        return None
    match = _CHATGPT_CONV.search(url)
    if not match:
        return None
    slug = match.group(1).strip("/")
    return slug or None
