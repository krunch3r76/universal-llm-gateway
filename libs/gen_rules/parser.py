"""Source-file parser for `agent-surface/sources/*.md`.

Parses target-tagged blocks and the optional `frontmatter:cursor` block.
All non-blank content outside a recognized block is a parser error — this prevents
silent drift when content is added to a source without picking a target.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_TARGETS = ("cursor", "*")
WILDCARD = "*"

_TARGET_OPEN = re.compile(r"<!--\s*target:(cursor|\*)\s*-->")
_TARGET_CLOSE = re.compile(r"<!--\s*/target:(cursor|\*)\s*-->")
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


def parse_source(path: Path) -> ParsedSource:
    """Parse a source file into frontmatter + target blocks."""
    text = path.read_text()
    lines = text.splitlines(keepends=True)
    parsed = ParsedSource(path=path)

    i = 0
    while i < len(lines):
        line = lines[i]
        line_no = i + 1

        fm_open = _FRONTMATTER_OPEN.match(line.strip())
        if fm_open:
            fm_lines: list[str] = []
            i += 1
            while i < len(lines):
                if _FRONTMATTER_CLOSE.match(lines[i].strip()):
                    parsed.frontmatter_cursor = "".join(fm_lines)
                    i += 1
                    break
                fm_lines.append(lines[i])
                i += 1
            continue

        open_m = _TARGET_OPEN.match(line.strip())
        if open_m:
            target = open_m.group(1)
            block_lines: list[str] = []
            i += 1
            while i < len(lines):
                close_m = _TARGET_CLOSE.match(lines[i].strip())
                if close_m and close_m.group(1) == target:
                    parsed.blocks.append(
                        Block(
                            target=target, content="".join(block_lines), line_no=line_no
                        )
                    )
                    i += 1
                    break
                block_lines.append(lines[i])
                i += 1
            else:
                raise ValueError(f"{path}:{line_no}: unclosed target:{target} block")
            continue

        unknown = _UNKNOWN_TARGET.match(line.strip())
        if unknown:
            raise ValueError(
                f"{path}:{line_no}: unknown target {unknown.group(1)!r} "
                f"(allowed: {ALLOWED_TARGETS})"
            )

        if line.strip():
            raise ValueError(
                f"{path}:{line_no}: content outside target block "
                "(all content must be inside <!-- target:X --> ... <!-- /target:X -->)"
            )
        i += 1

    return parsed
