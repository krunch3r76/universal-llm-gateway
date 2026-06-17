"""Boot skills index sidecar path helpers."""

from __future__ import annotations


def skills_index_rel_path(seat_slug: str) -> str:
    return f"notes/system/boot/skills-index-{seat_slug}.md"


def skills_index_cortex_uri(seat_slug: str) -> str:
    return f"cortex:{skills_index_rel_path(seat_slug)}"


__all__ = [
    "skills_index_cortex_uri",
    "skills_index_rel_path",
]
