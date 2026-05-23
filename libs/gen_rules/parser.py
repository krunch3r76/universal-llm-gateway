"""Source-file parser for `agent-surface/sources/*.md`.

Parses target-tagged blocks and the optional `frontmatter:cursor` block.
All non-blank content outside a recognized block is a parser error — this prevents
silent drift when content is added to a source without picking a target.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_TARGETS = ("cursor", "grok-direct", "*")
WILDCARD = "*"

_TARGET_OPEN = re.compile(r"<!--\s*target:(cursor|grok-direct|\*)\s*-->")
_TARGET_CLOSE = re.compile(r"<!--\s*/target:(cursor|grok-direct|\*)\s*-->")
_FRONTMATTER_OPEN = re.compile(r"<!--\s*frontmatter:cursor\s*$")
_FRONTMATTER_CLOSE = re.compile(r"^-->\s*$")
_UNKNOWN_TARGET = re.compile(r"<!--\s*/?target:([^\s>]+)\s*-->")


@dataclass
class Block:
    target: str
    content: str
    line_no: int


@dataclass
class ParsedSource:
    path: Path
    frontmatter_cursor: str | None = None
    blocks: list[Block] = field(default_factory=list)

    def blocks_for(self, target: str) -> list[Block]:
        return [b for b in self.blocks if b.target == target or b.target == WILDCARD]


def _format_error(path: Path, line_no: int, msg: str) -> str:
    return f"ERROR: {path}:{line_no}: {msg}"


def parse_source(path: Path) -> ParsedSource:
    text = path.read_text()
    lines = text.splitlines(keepends=True)

    parsed = ParsedSource(path=path)
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        stripped = raw.strip()

        if _FRONTMATTER_OPEN.match(stripped):
            j = i + 1
            fm_lines: list[str] = []
            while j < n and not _FRONTMATTER_CLOSE.match(lines[j]):
                fm_lines.append(lines[j])
                j += 1
            if j >= n:
                raise ValueError(
                    _format_error(path, i + 1, "unclosed frontmatter:cursor block")
                )
            if parsed.frontmatter_cursor is not None:
                raise ValueError(
                    _format_error(path, i + 1, "duplicate frontmatter:cursor block")
                )
            parsed.frontmatter_cursor = "".join(fm_lines)
            i = j + 1
            continue

        m_open = _TARGET_OPEN.match(stripped)
        if m_open:
            target = m_open.group(1)
            close_re = re.compile(rf"<!--\s*/target:{re.escape(target)}\s*-->")
            j = i + 1
            body: list[str] = []
            while j < n and not close_re.match(lines[j].strip()):
                body.append(lines[j])
                j += 1
            if j >= n:
                raise ValueError(
                    _format_error(
                        path, i + 1, f"unclosed <!-- target:{target} --> block"
                    )
                )
            content = "".join(body)
            parsed.blocks.append(Block(target=target, content=content, line_no=i + 1))
            i = j + 1
            continue

        if _TARGET_CLOSE.match(stripped):
            raise ValueError(_format_error(path, i + 1, "stray closing target tag"))

        m_unknown = _UNKNOWN_TARGET.match(stripped)
        if m_unknown:
            bad = m_unknown.group(1)
            raise ValueError(
                _format_error(
                    path,
                    i + 1,
                    f"unknown target {bad!r} (allowed: {', '.join(ALLOWED_TARGETS)})",
                )
            )

        if stripped:
            raise ValueError(
                _format_error(
                    path,
                    i + 1,
                    "untagged content outside any <!-- target:X --> block",
                )
            )
        i += 1

    return parsed
