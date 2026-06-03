"""HTML-comment marker region extraction for 2-A v2 handoff close contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ExtractStatus = Literal["ok", "unresolved", "ambiguous"]


@dataclass(frozen=True)
class ExtractResult:
    """Result of ``extract_handoff_marker_region`` (2-A v2)."""

    pair_count: int
    body: str | None
    status: ExtractStatus


def _marker_patterns(label: str | None) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """Line-anchored HTML comment markers (whole line, optional outer whitespace)."""
    lab = label.strip() if label and label.strip() else None
    if lab:
        esc = re.escape(lab)
        start = re.compile(rf"^\s*<!--\s*handoff:{esc}:start\s*-->\s*$", re.IGNORECASE)
        end = re.compile(rf"^\s*<!--\s*handoff:{esc}:end\s*-->\s*$", re.IGNORECASE)
    else:
        start = re.compile(r"^\s*<!--\s*handoff:start\s*-->\s*$", re.IGNORECASE)
        end = re.compile(r"^\s*<!--\s*handoff:end\s*-->\s*$", re.IGNORECASE)
    return start, end


def extract_handoff_marker_region(text: str, label: str | None) -> ExtractResult:
    """Extract literal bytes between matched HTML-comment marker pairs (2-A v2).

    0 pairs → unresolved; 1 pair → body (or unresolved if empty); >1 → ambiguous.
    Unbalanced start (no closing end) → unresolved. Markers must occupy a full
    line; lines inside fenced code blocks are ignored.
    """
    start_re, end_re = _marker_patterns(label)
    lines = text.splitlines()
    pairs: list[tuple[int, int]] = []
    in_fence = False
    idx = 0
    while idx < len(lines):
        if lines[idx].strip().startswith("```"):
            in_fence = not in_fence
            idx += 1
            continue
        if in_fence:
            idx += 1
            continue
        if not start_re.match(lines[idx]):
            idx += 1
            continue
        end_idx = idx + 1
        while end_idx < len(lines) and not end_re.match(lines[end_idx]):
            end_idx += 1
        if end_idx >= len(lines):
            return ExtractResult(pair_count=0, body=None, status="unresolved")
        pairs.append((idx, end_idx))
        idx = end_idx + 1
    if not pairs:
        return ExtractResult(pair_count=0, body=None, status="unresolved")
    if len(pairs) > 1:
        return ExtractResult(pair_count=len(pairs), body=None, status="ambiguous")
    start_i, end_i = pairs[0]
    body = "\n".join(lines[start_i + 1 : end_i])
    if not body.strip():
        return ExtractResult(pair_count=1, body=None, status="unresolved")
    return ExtractResult(pair_count=1, body=body, status="ok")
