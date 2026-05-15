"""Shared validators and primitive types used by multiple model sections.

This package is the split of the historical monolithic ``models.py``
along its section-comment boundaries. Cross-section symbols that have
no natural home (the dropbox URI validators, the ``AssertionConfidence``
Literal used by both Card v0 and the assertion models) live here so
neither side has to import from the other.
"""

from __future__ import annotations

from typing import Literal


def reject_cortex_dropbox_source_uri(value: str | None) -> str | None:
    """Reject source_uri values that point into the cortex sandbox dropbox.

    dropbox/ in the cortex sandbox is a temporary, non-persistent staging area.
    Entities MUST reference permanent paths — the ingest flow is to read from
    dropbox, move to a permanent location, then record the permanent path.

    Accepts raw sandbox paths (``dropbox/...``) as well as ``files://`` URI
    forms (``files://dropbox/...``, ``files:///dropbox/...``). External URLs
    that merely contain the substring "dropbox" (e.g. ``https://dropbox.com/x``)
    are unaffected.
    """
    if value is None:
        return value
    normalized = value.removeprefix("files://").lstrip("/")
    first_segment = normalized.split("/", 1)[0]
    if first_segment == "dropbox":
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
