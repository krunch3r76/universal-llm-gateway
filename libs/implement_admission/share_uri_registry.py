"""Frozen registry for cortex file-root top-level directories (Fork E).

Shared by ingress resolver, egress emitter, fs tool description, and tests.
Entity URIs (``cortex://todo/...``, ``cortex://service/...``) use type slugs
outside this set and are routed to entity resolution, not fs file ingress.
"""

from __future__ import annotations

CORTEX_FILE_ROOT_DIRS: frozenset[str] = frozenset(
    {
        "notes",
        "dropbox",
        "uploads",
        "exports",
        "trash",
        "agent-skills",
        ".shared-images",
    }
)


def is_cortex_entity_uri(rel_path: str) -> bool:
    """True when ``cortex://`` rel looks like an entity pointer, not a file path."""
    first = rel_path.strip("/").split("/", 1)[0]
    if not first:
        return False
    if ":" in first:
        return True
    return first not in CORTEX_FILE_ROOT_DIRS


__all__ = ["CORTEX_FILE_ROOT_DIRS", "is_cortex_entity_uri"]
