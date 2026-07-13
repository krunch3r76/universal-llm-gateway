"""Parse ``cortex_brief(...)`` directives embedded in cloud-proxy system prompts."""

from __future__ import annotations

import re
from typing import Any

_BOOT_CALL_RE = re.compile(r"""cortex_brief\s*\(\s*(?P<args>[^)]*)\s*\)""")

_KWARG_RE = re.compile(
    r"""(seat|agent|family|platform|role)\s*=\s*(["'])([\w-]+)\2""",
    re.IGNORECASE,
)

_PRIMARY_KEYS = ("seat", "agent", "family", "platform", "role")


def parse_boot_directive(content: str) -> tuple[str, dict[str, str]] | None:
    """Extract the first ``cortex_brief(...)`` call and its primary kwargs.

    Recognized shapes (aligned with MCP ``cortex_brief`` primary params):
      - ``cortex_brief(seat="<seat-slug>")`` — hyphenated slugs allowed
      - ``cortex_brief(agent="<seat-slug>")`` — permanent alias for ``seat``
      - ``cortex_brief(family="...", platform="...", role="...")`` — role optional

    Returns ``(matched_span, kwargs)`` when ``seat``, ``agent``, or ``family`` is
    present; otherwise ``None`` (prompt unchanged).

    Precedence for resolution lives server-side: ``seat`` / ``agent`` win over
    explicit ``family`` / ``platform`` when the slug parses as
    ``{family}-{platform}``. When both ``seat`` and ``agent`` are present,
    ``seat`` wins.
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
    """Build ``execute_tool("cortex_brief", ...)`` kwargs from parsed directive."""
    return {key: kwargs[key] for key in _PRIMARY_KEYS if key in kwargs}
