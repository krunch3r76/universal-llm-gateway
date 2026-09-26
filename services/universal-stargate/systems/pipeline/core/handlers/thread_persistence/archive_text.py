"""Resolve assistant archive text after frontier tool-loop completions.

When ``frontier_dispatch_v1`` finishes a native MCP tool loop with tool
activity but no final assistant text (common on early turns), the
archive step must still persist a replayable claim. This module
synthesizes a compact textual summary from the tool-call trace so
``assistant_turn(N)`` assertions land and ``build_referential_window``
can reconstruct history.
"""

from __future__ import annotations

from typing import Any

_TOOL_ONLY_PREFIX = "[Tool loop — archived activity]"


def synthesize_assistant_archive_text(
    content: str,
    tool_calls: list[Any],
) -> str:
    """Return text safe to archive as ``assistant_turn(N)``, synthesizing it from tool
    calls.

    Called by the ``archive_assistant_turn`` handler. Non-blank *content* is
    returned stripped. When content is blank and *tool_calls* is non-empty, builds
    a summary headed by the tool-loop marker prefix with one line per dict call
    (turn, name, ok/fail, first 240 chars of result). Returns "" when there is
    neither content nor tool calls.
    """
    text = (content or "").strip()
    if text:
        return text
    if not tool_calls:
        return ""

    lines = [_TOOL_ONLY_PREFIX]
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        name = str(tc.get("name") or "?")
        turn = tc.get("turn", "?")
        ok = tc.get("ok", True)
        status = "ok" if ok else "fail"
        result_preview = str(tc.get("result") or "")[:240].replace("\n", " ")
        lines.append(f"- turn {turn}: {name} ({status}): {result_preview}")
    return "\n".join(lines) if len(lines) > 1 else _TOOL_ONLY_PREFIX


def is_tool_synthesized_archive_text(content: str) -> bool:
    """Detect whether archived text is a tool-loop synthesis rather than real model
    output.

    Returns True when *content* starts with the marker prefix emitted by
    :func:`synthesize_assistant_archive_text`; ``archive_assistant_turn`` uses
    this to flag synthesized archives. ``None``/empty content yields False.
    """
    return (content or "").startswith(_TOOL_ONLY_PREFIX)
