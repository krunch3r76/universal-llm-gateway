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
    """Return archiveable assistant text, synthesizing from tools when needed."""
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
    """True when *content* was produced by :func:`synthesize_assistant_archive_text`."""
    return (content or "").startswith(_TOOL_ONLY_PREFIX)
