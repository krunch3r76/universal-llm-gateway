"""Bind monotonic capacity deadlines from client timeout hints (X-Request-Timeout)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import RequestContext


def _queue_timeout_s(routing_config: dict | None) -> float:
    rq = (routing_config or {}).get("request_queue", {})
    raw = rq.get("queue_timeout", 1800.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1800.0
    return value if value > 0 else 1800.0


def apply_capacity_deadline(
    context: RequestContext,
    *,
    routing_config: dict | None,
    request_timeout_hint: float | None,
) -> float:
    """Publish ``_capacity_deadline_mono`` and return effective budget seconds."""
    capacity_timeout_s = _queue_timeout_s(routing_config)
    retry_started = time.monotonic()
    effective = capacity_timeout_s
    if request_timeout_hint is not None and request_timeout_hint > 0:
        effective = min(request_timeout_hint, capacity_timeout_s)
    context._capacity_deadline_mono = retry_started + effective
    context.queue_wait_timeout_s = effective
    if request_timeout_hint is not None and request_timeout_hint > 0:
        context.request_timeout_hint = request_timeout_hint
    return effective
