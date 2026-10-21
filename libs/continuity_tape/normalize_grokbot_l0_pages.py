"""Strip ReadTranscript wrapper lines and align page header totals for L0 harvest."""

from __future__ import annotations

import re
from dataclasses import dataclass

from continuity_tape.extract_grokbot_jsonl import HEADER_RE, _physical_lines

_WRAPPER_OPEN = re.compile(
    r"^\s*<cursor_untrusted_data_\d+\s+source=\"ReadTranscript\">\s*$"
)
_WRAPPER_PAIR = re.compile(
    r"^\s*</cursor_untrusted_data_\d+><cursor_untrusted_data_\d+"
    r'\s+source="ReadTranscript">\s*$'
)
_WRAPPER_CLOSE = re.compile(r"^\s*</cursor_untrusted_data_\d+>\s*$")
_HEADER_TOTAL = re.compile(
    r"^(\s*(?:Transcript|Messages) of .+, positions (\d+)\s*"
    r"[\u2013\u2014-]\s*(\d+) of )(\d+)(:\s*)$"
)


class L0NormalizeError(ValueError):
    """L0 pages failed wrapper strip or role-line checks."""


@dataclass(frozen=True)
class L0NormalizeStats:
    pages: int
    wrapper_lines_stripped: int
    header_total_aligned: int | None
    role_lines: int


def _is_wrapper_only(line: str) -> bool:
    if line.startswith('{"role"'):
        return False
    text = line.strip()
    if not text:
        return False
    return bool(
        _WRAPPER_OPEN.match(text)
        or _WRAPPER_PAIR.match(text)
        or _WRAPPER_CLOSE.match(text)
    )


def _align_header_totals(lines: list[str], anchor_total: int) -> tuple[list[str], int]:
    changed = 0
    out: list[str] = []
    for line in lines:
        m = _HEADER_TOTAL.match(line)
        if m and int(m.group(4)) != anchor_total:
            out.append(f"{m.group(1)}{anchor_total}{m.group(5)}")
            changed += 1
            continue
        out.append(line)
    return out, changed


def normalize_grokbot_l0_pages(
    text: str, *, anchor_total: int | None = None
) -> tuple[str, L0NormalizeStats]:
    """Return normalized pages text and stats.

    Strips wrapper-only lines (opening tag, close+open pairs between pages,
    final close). Lines starting with ``{"role"`` are never stripped, even when
    they contain wrapper-like substrings. Aligns every page header ``of N:`` to
    the total from the first page header in the file (newest fetched page).
    """
    lines = _physical_lines(text)
    role_before = [ln for ln in lines if ln.startswith('{"role"')]
    stripped = 0
    kept: list[str] = []
    for line in lines:
        if _is_wrapper_only(line):
            stripped += 1
            continue
        kept.append(line)
    if anchor_total is None:
        for line in kept:
            match = HEADER_RE.match(line)
            if match:
                anchor_total = int(match.group(4))
                break
    if anchor_total is None:
        raise L0NormalizeError("no ReadTranscript page header found")
    aligned, n_changed = _align_header_totals(kept, anchor_total)
    role_after = [ln for ln in aligned if ln.startswith('{"role"')]
    if role_before != role_after:
        raise L0NormalizeError('{"role" lines changed order or bytes during normalize')
    pages = sum(1 for ln in aligned if HEADER_RE.match(ln))
    while aligned and not aligned[-1].strip():
        aligned.pop()
    body = "\n".join(aligned)
    if aligned:
        body += "\n"
    stats = L0NormalizeStats(
        pages=pages,
        wrapper_lines_stripped=stripped,
        header_total_aligned=anchor_total if n_changed else None,
        role_lines=len(role_after),
    )
    return body, stats


def normalized_capture_ref(l0_ref: str) -> str:
    """Derive the normalized capture ref from a raw ``*.pages.txt`` ref."""
    text = l0_ref.strip()
    for prefix in ("cortex://", "workspaces://"):
        if text.startswith(prefix):
            base = text.removeprefix(prefix)
            if base.endswith(".pages.txt"):
                base = base[: -len(".pages.txt")] + ".normalized.pages.txt"
            else:
                base = base + ".normalized.pages.txt"
            return prefix + base
    if text.endswith(".pages.txt"):
        return text[: -len(".pages.txt")] + ".normalized.pages.txt"
    return text + ".normalized.pages.txt"


__all__ = [
    "L0NormalizeError",
    "L0NormalizeStats",
    "normalize_grokbot_l0_pages",
    "normalized_capture_ref",
]
