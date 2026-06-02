"""Parse ``cortex_boot(...)`` directives embedded in cloud-proxy system prompts."""

from __future__ import annotations

import re
from typing import Any

_BOOT_CALL_RE = re.compile(r"""cortex_boot\s*\(\s*(?P<args>[^)]*)\s*\)""")

_KWARG_RE = re.compile(
    r"""(agent|family|platform|role)\s*=\s*(["'])([\w-]+)\2""",
    re.IGNORECASE,
)

_PRIMARY_KEYS = ("agent", "family", "platform", "role")


def parse_boot_directive(content: str) -> tuple[str, dict[str, str]] | None:
    """Extract the first ``cortex_boot(...)`` call and its primary kwargs.

    Recognized shapes (aligned with MCP ``cortex_boot`` primary params):
      - ``cortex_boot(agent="<seat-slug>")`` — hyphenated slugs allowed
      - ``cortex_boot(family="...", platform="...", role="...")`` — role optional

    Returns ``(matched_span, kwargs)`` when ``agent`` or ``family`` is present;
    otherwise ``None`` (prompt unchanged).

    Precedence for resolution lives server-side: ``agent`` wins over explicit
    ``family`` / ``platform`` when the slug parses as ``{family}-{platform}``.
    """
    match = _BOOT_CALL_RE.search(content)
    if not match:
        return None
    kwargs: dict[str, str] = {}
    for kw_match in _KWARG_RE.finditer(match.group("args")):
        kwargs[kw_match.group(1).lower()] = kw_match.group(3)
    if "agent" not in kwargs and "family" not in kwargs:
        return None
    return match.group(0), kwargs


def boot_tool_arguments(kwargs: dict[str, str]) -> dict[str, Any]:
    """Build ``execute_tool("cortex_boot", ...)`` kwargs from parsed directive."""
    return {key: kwargs[key] for key in _PRIMARY_KEYS if key in kwargs}
