"""Canonical URL normalization for durable Claude session joins.

Registry, orphan, occupancy, and follow-up surfaces use the same normalized
key so a fragment or trailing slash cannot create a second CSE identity.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def normalize_cse_url(url: str) -> str:
    """Return a stable CSE URL key without fragments or trailing path slashes."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    path = parts.path.rstrip("/") or parts.path
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))
