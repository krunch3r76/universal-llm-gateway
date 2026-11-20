"""Schedule and run observe-only route-probe evaluation at implement closeout ingress.

observe-only: never refuses, rewrites, delays or fails a closeout

Leg E2.1: dedicated one-worker ``ThreadPoolExecutor``; when a job is reserved or
running, further schedules are dropped and emit ``check.failed`` with
``error_type=ObserveJobDropped``. The executor is never shut down and lives for
the process. The future from ``run_in_executor`` must never be cancelled.
``observe_executor`` exists for tests only.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import hashlib
import json
import threading
import time
from collections.abc import Callable
from functools import partial
from typing import Any

from implement_admission.closeout_route_probe_gate import (
    RouteProbeObserveVerdict,
    evaluate_closeout_route_probes,
)
from universal_logging import get_logger

from .closeout_route_probe_observe_events import (
    emit_closeout_routeprobe_check_failed,
    emit_closeout_routeprobe_evaluated,
)

logger = get_logger(__name__)

CLOSEOUT_DEEP_COPY_WARN_CHARS: int = 262144
_OBSERVE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="closeout_route_probe_observe"
)
_OBSERVE_LOCK = threading.Lock()
_observe_in_flight: bool = False
_observe_dropped_total: int = 0

def schedule_closeout_route_probe_observe(
    *,
    request_id: str,
    idempotency_key: str | None,
    source_ref: str | None,
    closeout: dict[str, Any],
    observe_executor: concurrent.futures.Executor | None = None,
) -> None:
    """Enqueue observe-only route-probe evaluation on the dedicated executor.

    observe-only: never refuses, rewrites, delays or fails a closeout
    """
    global _observe_in_flight, _observe_dropped_total
    ingress = "keyed" if idempotency_key else "unkeyed"
    chosen = observe_executor if observe_executor is not None else _OBSERVE_EXECUTOR
    try:
        loop = asyncio.get_running_loop()
        snapshot = copy.deepcopy(closeout)
        with _OBSERVE_LOCK:
            dropped = _observe_in_flight
            if dropped:
                _observe_dropped_total += 1
            else:
                _observe_in_flight = True
        if dropped:
            logger.warning(
                "closeout route-probe observe: dropped, executor busy request_id=%s "
                "idempotency_key=%s",
                request_id,
                idempotency_key,
            )
            emit_closeout_routeprobe_check_failed(
                request_id=request_id,
                idempotency_key=idempotency_key,
                source_ref=source_ref,
                ingress=ingress,
                stage="schedule",
                error_type="ObserveJobDropped",
                error="observe executor saturated",
                loop=None,
            )
            return
        try:
            loop.run_in_executor(
                chosen,
                partial(
                    _run_observe_wrapped,
                    request_id=request_id,
                    idempotency_key=idempotency_key,
                    source_ref=source_ref,
                    closeout=snapshot,
                    loop=loop,
                ),
            )
        except BaseException:
            with _OBSERVE_LOCK:
                _observe_in_flight = False
            raise
    except Exception as exc:
        logger.warning(
            "closeout route-probe observe: schedule failed request_id=%s "
            "idempotency_key=%s error_type=%s",
            request_id,
            idempotency_key,
            type(exc).__name__,
        )
        _emit_check_failed_guarded(
            request_id=request_id,
            idempotency_key=idempotency_key,
            source_ref=source_ref,
            ingress=ingress,
            stage="schedule",
            exc=exc,
            loop=None,
        )


def _run_observe_wrapped(
    *,
    request_id: str,
    idempotency_key: str | None,
    source_ref: str | None,
    closeout: dict[str, Any],
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Worker entry: size warning, then run the observe job and clear reservation.

    observe-only: never refuses, rewrites, delays or fails a closeout
    """
    global _observe_in_flight
    try:
        try:
            chars = len(json.dumps(closeout, default=str))
        except Exception:
            chars = -1
        if chars > CLOSEOUT_DEEP_COPY_WARN_CHARS:
            logger.warning(
                "closeout route-probe observe: large closeout request_id=%s "
                "chars=%d fields=%d",
                request_id,
                chars,
                len(closeout),
            )
        run_closeout_route_probe_observe(
            request_id=request_id,
            idempotency_key=idempotency_key,
            source_ref=source_ref,
            closeout=closeout,
            loop=loop,
        )
    finally:
        with _OBSERVE_LOCK:
            _observe_in_flight = False


def run_closeout_route_probe_observe(
    *,
    request_id: str,
    idempotency_key: str | None,
    source_ref: str | None,
    closeout: Any,
    evaluate: Callable[[Any], RouteProbeObserveVerdict] = evaluate_closeout_route_probes,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Evaluate closeout route-probe evidence and publish the observation event.

    observe-only: never refuses, rewrites, delays or fails a closeout
    """
    ingress = "keyed" if idempotency_key else "unkeyed"
    stage = "evaluate"
    try:
        closeout_digest = hashlib.sha256(
            json.dumps(
                closeout, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
        ).hexdigest()[:16]
        started = time.monotonic()
        verdict = evaluate(closeout)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stage = "emit"
        lines = residue_lines_for(verdict)
        for line in lines:
            logger.warning(
                "request_id=%s idempotency_key=%s %s",
                request_id,
                idempotency_key,
                line,
            )
        would_have_refused = (
            verdict.would_require and verdict.satisfied is False
        )
        emit_closeout_routeprobe_evaluated(
            request_id=request_id,
            idempotency_key=idempotency_key,
            source_ref=source_ref,
            ingress=ingress,
            closeout_digest=closeout_digest,
            would_require=verdict.would_require,
            satisfied=verdict.satisfied,
            would_have_refused=would_have_refused,
            arms=list(verdict.arms),
            required=list(verdict.required),
            missing=list(verdict.missing),
            unmapped=list(verdict.unmapped),
            path_prefix_only=list(verdict.path_prefix_only),
            residue_lines=lines,
            reach_errors=list(verdict.reach_errors),
            input_defects=list(verdict.input_defects),
            elapsed_ms=elapsed_ms,
            loop=loop,
        )
    except Exception as exc:
        logger.warning(
            "closeout route-probe observe: check failed stage=%s request_id=%s "
            "idempotency_key=%s error_type=%s error=%s",
            stage,
            request_id,
            idempotency_key,
            type(exc).__name__,
            str(exc)[:300],
        )
        _emit_check_failed_guarded(
            request_id=request_id,
            idempotency_key=idempotency_key,
            source_ref=source_ref,
            ingress=ingress,
            stage=stage,
            exc=exc,
            loop=loop,
        )


def residue_lines_for(verdict: RouteProbeObserveVerdict) -> list[str]:
    """Build human-readable residue warning lines from a route-probe verdict."""
    lines: list[str] = []
    required_by_slug = {r["slug"]: r for r in verdict.required}
    for miss in verdict.missing:
        slug = miss["slug"]
        req = required_by_slug.get(slug, {})
        arms = ",".join(
            a
            for a, held in (("named", req.get("named")), ("reached", req.get("reached") is True))
            if held
        )
        suffix = (
            f":{miss['parse_refusal']}"
            if miss.get("parse_refusal")
            else ""
        )
        reason = miss.get("reason_code", "")
        canonical = miss.get("canonical_command", "")
        lines.append(
            f"route_probe_observe: {slug} — would_require ({arms}); "
            f"evidence={reason}{suffix}; canonical={canonical}"
        )
    return lines


def _emit_check_failed_guarded(
    *,
    request_id: str,
    idempotency_key: str | None,
    source_ref: str | None,
    ingress: str,
    stage: str,
    exc: BaseException,
    loop: asyncio.AbstractEventLoop | None,
) -> None:
    try:
        emit_closeout_routeprobe_check_failed(
            request_id=request_id,
            idempotency_key=idempotency_key,
            source_ref=source_ref,
            ingress=ingress,
            stage=stage,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
            loop=loop,
        )
    except Exception as exc2:
        logger.warning(
            "closeout route-probe observe: check.failed emit failed "
            "request_id=%s error_type=%s",
            request_id,
            type(exc2).__name__,
        )