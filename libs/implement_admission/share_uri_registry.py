"""Existence-first cortex file vs entity discriminator for Share URIs.

Leading segment under ``CORTEX_FILES_ROOT`` that ``.exists()`` (file or dir)
⇒ file path; otherwise entity (read path). Colon in the leading segment
forces entity. Write-side top-level creation of a *new* root entry is rejected
at ingress (see ``top_level_creation_error``) — nested writes under an
existing top-level are file paths.
"""

from __future__ import annotations

from pathlib import Path


def leading_segment(rel_path: str) -> str:
    """Return the first slash-separated segment of a cortex-relative path."""
    return rel_path.strip("/").split("/", 1)[0]


def top_level_creation_error(first: str) -> ValueError:
    """Teaching error when slash-form write targets an absent top-level entry."""
    return ValueError(
        f"Creating a new top-level cortex file-root entry {first!r} requires an "
        "explicit create_root/mkdir op or flag; slash-form write to an absent "
        "top-level is rejected. Nested writes under an existing top-level are "
        "allowed."
    )


def entity_vs_file_teaching_error(raw: str, first: str) -> ValueError:
    """Teaching error when a cortex:// path is classified as entity, not fs."""
    return ValueError(
        f"cortex:// path {raw!r} looks like an entity pointer (leading segment "
        f"{first!r} does not exist under the cortex files root). "
        "File paths require an existing top-level entry "
        "(existence-first via .exists()); force entity form with a colon "
        f"(cortex://{first}:slug). Entity URIs are resolved via cortex entity "
        "lookup, not fs."
    )


def is_cortex_entity_uri(
    rel_path: str,
    *,
    cortex_root: Path,
    for_write: bool = False,
) -> bool:
    """True when ``cortex://`` rel looks like an entity pointer, not a file path.

    Ordering: colon ≻ for_write top-level gate ≻ ``.exists()`` file-wins.
    ``cortex_root`` is required — callers must inject the root (no silent
    live-mount fallback inside this predicate).
    """
    first = leading_segment(rel_path)
    if not first:
        return False
    if ":" in first:
        return True
    root = cortex_root.resolve()
    top = root / first
    if for_write:
        if top.exists():
            return False
        raise top_level_creation_error(first)
    return not top.exists()


__all__ = [
    "entity_vs_file_teaching_error",
    "is_cortex_entity_uri",
    "leading_segment",
    "top_level_creation_error",
]
