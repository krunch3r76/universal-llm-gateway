"""Observe-only route-probe check signals at implement closeout ingress.

observe-only: never refuses, rewrites, delays or fails a closeout
"""

from __future__ import annotations

import asyncio
from typing import Any

from universal_event_bus import Event, event_factory

from .cursor_sdk_generate_signals import publish_frontier_event


@event_factory
def FrontierCloseoutRouteprobeEvaluated(  # noqa: N802
    *,
    request_id: str,
    idempotency_key: str | None,
    source_ref: str | None,
    ingress: str,
    closeout_digest: str,
    would_require: bool,
    satisfied: bool | None,
    would_have_refused: bool,
    arms: list[str],
    required: list[dict[str, Any]],
    missing: list[dict[str, Any]],
    unmapped: list[str],
    path_prefix_only: list[str],
    residue_lines: list[str],
    reach_errors: list[dict[str, str]],
    input_defects: list[str],
    elapsed_ms: int,
) -> Event:
    """Closeout ingress call evaluated for route-probe evidence; observe only, nothing refused."""
    return Event(
        signal="frontier.closeout.routeprobe.evaluated",
        payload={
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "source_ref": source_ref,
            "ingress": ingress,
            "closeout_digest": closeout_digest,
            "mode": "observe",
            "would_require": would_require,
            "satisfied": satisfied,
            "would_have_refused": would_have_refused,
            "arms": arms,
            "required": required,
            "missing": missing,
            "unmapped": unmapped,
            "path_prefix_only": path_prefix_only,
            "residue_lines": residue_lines,
            "reach_errors": reach_errors,
            "input_defects": input_defects,
            "elapsed_ms": elapsed_ms,
        },
        role="observation",
        scope="node",
    )


@event_factory
def FrontierCloseoutRouteprobeCheckFailed(  # noqa: N802
    *,
    request_id: str,
    idempotency_key: str | None,
    source_ref: str | None,
    ingress: str,
    stage: str,
    error_type: str,
    error: str,
) -> Event:
    """Route-probe observe check raised; the closeout itself is unaffected."""
    return Event(
        signal="frontier.closeout.routeprobe.check.failed",
        payload={
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "source_ref": source_ref,
            "ingress": ingress,
            "mode": "observe",
            "stage": stage,
            "error_type": error_type,
            "error": error,
        },
        role="observation",
        scope="node",
    )


def emit_closeout_routeprobe_evaluated(
    *, loop: asyncio.AbstractEventLoop | None = None, **kwargs: Any
) -> None:
    """Publish evaluated signal synchronously when loop is None, else via call_soon_threadsafe."""
    event = FrontierCloseoutRouteprobeEvaluated(**kwargs)
    if loop is None:
        publish_frontier_event(event)
    else:
        loop.call_soon_threadsafe(publish_frontier_event, event)


def emit_closeout_routeprobe_check_failed(
    *, loop: asyncio.AbstractEventLoop | None = None, **kwargs: Any
) -> None:
    """Publish check.failed synchronously when loop is None, else via call_soon_threadsafe."""
    event = FrontierCloseoutRouteprobeCheckFailed(**kwargs)
    if loop is None:
        publish_frontier_event(event)
    else:
        loop.call_soon_threadsafe(publish_frontier_event, event)


__all__ = [
    "FrontierCloseoutRouteprobeCheckFailed",
    "FrontierCloseoutRouteprobeEvaluated",
    "emit_closeout_routeprobe_check_failed",
    "emit_closeout_routeprobe_evaluated",
]