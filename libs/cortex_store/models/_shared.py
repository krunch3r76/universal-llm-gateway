"""Shared validators and primitive types used by multiple model sections.

This package is the split of the historical monolithic ``models.py``
along its section-comment boundaries. Cross-section symbols that have
no natural home (the dropbox URI validators, the ``AssertionConfidence``
Literal used by both Card v0 and the assertion models) live here so
neither side has to import from the other.
"""

from __future__ import annotations

from typing import Literal

STAGING_PREFIXES: frozenset[str] = frozenset({"dropbox"})

_KNOWN_INTERNAL_SCHEMES = ("files://", "cortex://", "ws://", "workspaces://")
_WORKSPACE_SCHEMES = frozenset({"workspaces://", "ws://"})


def first_segment_after_internal_normalize(value: str) -> str | None:
    """Return the first path segment after scheme + workspace authority strip.

    External ``http(s)://`` URLs return ``None`` (host segments are not path
    staging roots). Used by the pydantic hard-reject and advisory attr scans.
    """
    uri = value.strip()
    if not uri:
        return None

    lower = uri.lower()
    if lower.startswith(("http://", "https://")):
        return None

    stripped_workspace = False
    for prefix in _KNOWN_INTERNAL_SCHEMES:
        if lower.startswith(prefix):
            stripped_workspace = prefix in _WORKSPACE_SCHEMES
            uri = uri[len(prefix) :]
            break

    uri = uri.lstrip("/")
    if not uri:
        return None

    parts = uri.split("/")
    if stripped_workspace and len(parts) >= 2:
        parts = parts[1:]

    if not parts or not parts[0]:
        return None
    return parts[0]


def uri_first_segment_is_staging(value: str) -> bool:
    segment = first_segment_after_internal_normalize(value)
    return segment in STAGING_PREFIXES if segment else False


def reject_cortex_dropbox_source_uri(value: str | None) -> str | None:
    """Reject source_uri values that point into the cortex sandbox dropbox.

    dropbox/ in the cortex sandbox is a temporary, non-persistent staging area.
    Entities MUST reference permanent paths — the ingest flow is to read from
    dropbox, move to a permanent location, then record the permanent path.

    Normalizes internal schemes (``files://``, ``cortex://``, ``ws://``,
    ``workspaces://``), strips workspace authority when present, then matches
    ``STAGING_PREFIXES`` at the **first remaining segment** (segment-exact).
    External URLs (e.g. ``https://dropbox.com/x``) are unaffected.
    """
    if value is None:
        return value
    if uri_first_segment_is_staging(value):
        raise ValueError(
            "URI must not point into the cortex sandbox dropbox "
            "(temporary, non-persistent staging). Move the file to a "
            "permanent path and record that path instead. "
            f"Rejected: {value!r}"
        )
    return value


def reject_cortex_dropbox_uri_list(value: list[str] | None) -> list[str] | None:
    """Apply dropbox rejection to each element of a URI list field."""
    if value is None:
        return value
    for uri in value:
        reject_cortex_dropbox_source_uri(uri)
    return value


AssertionConfidence = Literal["confirmed", "believed", "suspected", "hypothesized"]

__all__ = [
    "AssertionConfidence",
    "STAGING_PREFIXES",
    "first_segment_after_internal_normalize",
    "reject_cortex_dropbox_source_uri",
    "reject_cortex_dropbox_uri_list",
    "uri_first_segment_is_staging",
]
