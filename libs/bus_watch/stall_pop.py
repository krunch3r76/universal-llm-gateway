"""Machine-readable stall-pop line for IDE notify_on_output."""

from __future__ import annotations

import sys
from typing import TextIO

_STALL_POP_PREFIX = "stall-pop:"


def should_emit_stall_pop(
    *,
    last_reason: str | None,
    reason: str,
    stall_active: bool,
) -> tuple[bool, str | None]:
    """A-4 debounce: ≤1 stall-pop line per stall episode (resets when stall clears)."""
    if not stall_active or not reason.strip():
        return False, None
    if reason == last_reason:
        return False, last_reason
    return True, reason


def format_producer_terminal_reason(
    producer: dict[str, object] | None,
    *,
    base: str = "producer_terminal_no_reply",
) -> str:
    """Append ``archive_uri=`` when a failed terminal producer carries harvest proof."""
    if not producer:
        return base
    if producer.get("terminal_status") != "failed":
        return base
    archive_uri = producer.get("archive_uri")
    if not archive_uri:
        return base
    return f"{base} archive_uri={archive_uri}"


def emit_stall_pop(reason: str, *, stream: TextIO | None = None) -> None:
    """Print machine line ``stall-pop: <reason>`` flush=True for notify_on_output."""
    out = stream or sys.stdout
    text = reason.strip()
    print(f"{_STALL_POP_PREFIX} {text}", flush=True, file=out)
