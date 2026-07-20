"""Parse ``cortex_brief(...)`` directives embedded in cloud-proxy system prompts."""

from __future__ import annotations

import re
from typing import Any

_BOOT_CALL_RE = re.compile(r"""cortex_brief\s*\(\s*(?P<args>[^)]*)\s*\)""")

# Legacy prompt kwargs remain parseable; boot_tool_arguments maps them to seat=.
_KWARG_RE = re.compile(
    r"""(seat|agent|family|platform|role)\s*=\s*(["'])([\w-]+)\2""",
    re.IGNORECASE,
)

_PRIMARY_KEYS = ("seat", "role")


def parse_boot_directive(content: str) -> tuple[str, dict[str, str]] | None:
    """Extract the first ``cortex_brief(...)`` call and its primary kwargs.

    Recognized shapes:
      - ``cortex_brief(seat="<seat-slug>")`` — current wire form
      - ``cortex_brief(agent="<seat-slug>")`` — legacy → ``seat``
      - ``cortex_brief(family="...", platform="...")`` — legacy → ``seat={family}-{platform}``

    Returns ``(matched_span, kwargs)`` when ``seat``, ``agent``, or ``family`` is
    present; otherwise ``None`` (prompt unchanged).
    """
    match = _BOOT_CALL_RE.search(content)
    if not match:
        return None
    kwargs: dict[str, str] = {}
    for kw_match in _KWARG_RE.finditer(match.group("args")):
        kwargs[kw_match.group(1).lower()] = kw_match.group(3)
    if "seat" not in kwargs and "agent" not in kwargs and "family" not in kwargs:
        return None
    return match.group(0), kwargs


def boot_tool_arguments(kwargs: dict[str, str]) -> dict[str, Any]:
    """Build ``execute_tool("cortex_brief", ...)`` kwargs from parsed directive.

    Legacy ``agent=`` / ``family=``+``platform=`` in prompt text are normalized
    to ``seat=`` — cortex_brief no longer accepts those identity axes on the wire.
    """
    out: dict[str, Any] = {}
    if "seat" in kwargs:
        out["seat"] = kwargs["seat"]
    elif "agent" in kwargs:
        out["seat"] = kwargs["agent"]
    elif "family" in kwargs and "platform" in kwargs:
        out["seat"] = f"{kwargs['family'].lower()}-{kwargs['platform'].lower()}"
    elif "family" in kwargs:
        # Incomplete legacy shape — pass as seat slug attempt (may seat_unresolved).
        out["seat"] = kwargs["family"].lower()
    if "role" in kwargs:
        out["role"] = kwargs["role"]
    return out
