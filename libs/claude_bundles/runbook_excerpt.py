"""Pure ATX section extraction from runbook markdown — no I/O."""

from __future__ import annotations

import re

_ATX_L2_RE = re.compile(r"^#{1,2}\s")


def extract_sections(markdown: str, headings: tuple[str, ...]) -> str:
    """Return requested ``## heading`` sections in order, each with its ATX line.

    Body runs until the next ATX heading of level ≤ 2. Missing *heading* raises
    ``ValueError``.
    """
    text = markdown or ""
    lines = text.splitlines()
    pieces: list[str] = []
    for heading in headings:
        want = f"## {heading}"
        start: int | None = None
        for i, line in enumerate(lines):
            if line.strip() == want:
                start = i
                break
        if start is None:
            raise ValueError(f"missing heading: {heading!r}")
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if _ATX_L2_RE.match(lines[j]):
                end = j
                break
        section_lines = lines[start:end]
        pieces.append("\n".join(section_lines).rstrip())
    return "\n\n".join(pieces)


__all__ = ["extract_sections"]
