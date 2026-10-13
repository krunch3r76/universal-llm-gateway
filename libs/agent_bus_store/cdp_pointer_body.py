"""CDP generate pointer body format — no database imports."""

from __future__ import annotations

import re

CDP_POINTER_BODY_PREFIX = "CDP generate admitted"

_EXECUTION_ID_RE = re.compile(
    r"execution_id=([^)\s]+)\)",
)


def format_cdp_generate_pointer_body(*, model: str, execution_id: str) -> str:
    """Canonical CDP generate pointer body (must stay in sync with stargate)."""
    return (
        f"{CDP_POINTER_BODY_PREFIX} (model={model}, execution_id={execution_id}). "
        "Poll poll_hint (from_agent=web-anthropic). "
        "Terminal only after harvest proof."
    )


def parse_cdp_pointer_execution_id(body: str | None) -> str | None:
    """Extract execution_id from a CDP generate pointer body, or None."""
    if not body or not body.startswith(CDP_POINTER_BODY_PREFIX):
        return None
    match = _EXECUTION_ID_RE.search(body)
    if match is None:
        return None
    value = match.group(1).strip()
    return value or None
