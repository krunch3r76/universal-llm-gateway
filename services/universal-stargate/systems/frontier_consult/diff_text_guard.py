"""Detect unified-diff / patch bodies in model-facing packet text."""

from __future__ import annotations

import re

from .admission import FrontierEndpointError

_DIFF_GIT_RE = re.compile(r"^diff --git ")
_HUNK_HEADER_RE = re.compile(r"^@@ [-+0-9, ]+ @@")
_PATCH_FILE_HEADER_RE = re.compile(r"^(\+\+\+|---) [ab]/")
_PATCH_BODY_LINE_RE = re.compile(r"^[+-](?![+-])")


def packet_contains_diff_text(text: str) -> bool:
    """True when *text* carries real unified-diff structure (A1 conjunction)."""
    lines = text.splitlines()
    if not lines:
        return False

    for line in lines:
        if _DIFF_GIT_RE.match(line):
            return True

    for index, line in enumerate(lines):
        if not _HUNK_HEADER_RE.match(line):
            continue
        window_start = max(0, index - 5)
        window_end = min(len(lines), index + 6)
        for window_line in lines[window_start:window_end]:
            if _PATCH_FILE_HEADER_RE.match(window_line):
                return True
            if _PATCH_BODY_LINE_RE.match(window_line):
                return True

    index = 0
    while index < len(lines):
        if not _HUNK_HEADER_RE.match(lines[index]):
            index += 1
            continue
        cursor = index + 1
        consecutive_body = 0
        while cursor < len(lines):
            current = lines[cursor]
            if _HUNK_HEADER_RE.match(current) or _DIFF_GIT_RE.match(current):
                break
            if _PATCH_BODY_LINE_RE.match(current):
                consecutive_body += 1
                if consecutive_body >= 2:
                    return True
            else:
                consecutive_body = 0
            cursor += 1
        index += 1

    return False


def assert_packet_free_of_diff_text(
    *,
    request_id: str,
    packet_path: str,
    text: str,
) -> None:
    """Reject packets that embed unified-diff bodies before model ingress."""
    if not packet_contains_diff_text(text):
        return
    raise FrontierEndpointError(
        request_id=request_id,
        field="packet_path",
        reason=(
            f"Packet {packet_path!r} contains unified-diff or patch text; "
            "model-facing packets must not embed diff hunks. Use paths and "
            "whole-file reads instead."
        ),
        status_code=422,
        code="handoff_packet_contains_diff_text",
    )
