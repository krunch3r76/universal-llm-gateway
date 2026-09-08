"""Derive watcher verdict and emit script-level verdict-change lines."""

from __future__ import annotations

import sys
from typing import Any, Literal, TextIO

Verdict = Literal[
    "in_flight",
    "polling",
    "stalled",
    "terminal_no_reply",
    "unlinked",
    "unknown_producer",
]

_VERDICT_CHANGED_PREFIX = "verdict-changed:"


def derive_verdict(
    *,
    producer: dict[str, Any],
    producer_grace_expired: bool,
    stall_active: bool,
) -> Verdict:
    """Map producer projection + stall predicate to a single watcher verdict.

    Grammar (O14-D1): in-flight grace suppresses stall; stall wins over terminal
    classification when the predicate fires; otherwise producer state drives the
    advisory label; default is polling.
    """
    state = producer.get("state")
    if state == "in_flight" and not producer_grace_expired:
        return "in_flight"
    if stall_active:
        return "stalled"
    if state == "terminal":
        return "terminal_no_reply"
    if state == "unlinked":
        return "unlinked"
    if state == "unknown":
        return "unknown_producer"
    return "polling"


def should_emit_verdict_changed(
    *,
    last_verdict: str | None,
    verdict: str,
) -> tuple[bool, str]:
    """Return (emit, next_last) — at most one line per distinct verdict."""
    if not verdict.strip():
        return False, last_verdict
    if verdict == last_verdict:
        return False, last_verdict
    return True, verdict


def emit_verdict_changed(
    *,
    label: str,
    thread: str,
    execution_id: str,
    from_verdict: str,
    to_verdict: str,
    stream: TextIO | None = None,
) -> None:
    """Print ``verdict-changed:`` machine line for notify_on_output / event ingest."""
    out = stream or sys.stdout
    print(
        f"{_VERDICT_CHANGED_PREFIX} label={label} thread={thread} "
        f"execution_id={execution_id} from={from_verdict} to={to_verdict}",
        flush=True,
        file=out,
    )
