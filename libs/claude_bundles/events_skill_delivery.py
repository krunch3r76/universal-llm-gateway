"""Skill-delivery attest observation events (G1 Option A / invariant 4).

Publishes success **and** fail channel-attest outcomes to Event Service so
pass ≠ silence. Callers: ``attest_delivery_channels`` only — best-effort UDS
ingest must never override fail-closed attest semantics.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import socket
import threading
import time
from typing import Any

from claude_bundles.events_compose_attest import _resolve_tcp_target
from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory

logger = logging.getLogger(__name__)

_MIRROR_QUEUE_MAXSIZE = 256
_CIRCUIT_BREAKER_SECONDS = 30.0
_WARNING_INTERVAL_SECONDS = 300.0

_mirror_queue: queue.Queue[Any] = queue.Queue(maxsize=_MIRROR_QUEUE_MAXSIZE)
_mirror_worker_lock = threading.Lock()
_mirror_worker_started = False
_circuit_open_until = 0.0
_mirror_state_lock = threading.Lock()
# (transport, target) -> monotonic last warning time
_last_warning_at: dict[tuple[str, str], float] = {}
# (transport, target) -> (failures, drops) since last warning
_warning_counts: dict[tuple[str, str], tuple[int, int]] = {}
_circuit_skip_count = 0
_queue_drop_count = 0


class _MirrorFlush:
    """Test-only queue sentinel; worker sets ``done`` after draining ahead of it."""

    __slots__ = ("done",)

    def __init__(self) -> None:
        self.done = threading.Event()


@event_factory
def cdp_skill_delivery_attested(
    *,
    ok: bool,
    attached: list[str],
    inlined: list[str],
    undelivered: list[str],
    induction: list[str] | None = None,
    wrong_channel: list[str] | None = None,
    rows: list[dict[str, str]] | None = None,
    execution_id: str = "",
    satellite_execution_id: str = "",
    claude_surface: str = "",
    inline_observed: list[str] | None = None,
) -> Event:
    """Observation event for sealed CDP skill channel attest (success or fail).

    ``execution_id`` is the **Stargate** seating id (indexes ``$.execution_id``);
    ``satellite_execution_id`` is the cdp_ask admit id. Keys are always present;
    non-seating callers may leave both empty rather than inventing ids.
    """
    return Event(
        signal="cdp.skill.delivery_attested",
        role="observation",
        scope="node",
        payload={
            "ok": ok,
            "attached": list(attached),
            "inlined": list(inlined),
            "induction": list(induction or []),
            "undelivered": list(undelivered),
            "wrong_channel": list(wrong_channel or []),
            "rows": list(rows or []),
            "execution_id": str(execution_id or ""),
            "satellite_execution_id": str(satellite_execution_id or ""),
            "claude_surface": str(claude_surface or ""),
            "inline_observed": sorted(
                {str(s).strip() for s in (inline_observed or []) if str(s).strip()}
            ),
        },
    )


def emit_skill_delivery_attested(
    *,
    ok: bool,
    attached: list[str],
    inlined: list[str],
    undelivered: list[str],
    induction: list[str] | None = None,
    wrong_channel: list[str] | None = None,
    rows: list[dict[str, str]] | None = None,
    execution_id: str = "",
    satellite_execution_id: str = "",
    claude_surface: str = "",
    inline_observed: list[str] | None = None,
) -> Event | None:
    """Build + best-effort-mirror ``cdp.skill.delivery_attested``; never raises.

    Returns the Event when construction succeeds (tests assert payload); returns
    None only if factory construction itself fails (should not happen in prod).
    Payload ``execution_id`` must be Stargate-scoped (never the satellite admit id).
    """
    try:
        event = cdp_skill_delivery_attested(
            ok=ok,
            attached=attached,
            inlined=inlined,
            undelivered=undelivered,
            induction=induction,
            wrong_channel=wrong_channel,
            rows=rows,
            execution_id=execution_id,
            satellite_execution_id=satellite_execution_id,
            claude_surface=claude_surface,
            inline_observed=inline_observed,
        )
    except Exception:  # noqa: BLE001 — attest path must not fail on telemetry
        return None
    _mirror_to_event_service(event)
    return event


@event_factory
def cdp_skill_context_loaded(
    *,
    ok: bool,
    required: list[str],
    observed: list[str],
    missing: list[str],
    execution_id: str = "",
    satellite_execution_id: str = "",
    context_found: bool = False,
    skills_heading_found: bool = False,
    error: str = "",
) -> Event:
    """Post-submit Context → Skills receipt (non-gating observation)."""
    return Event(
        signal="cdp.skill.context_loaded",
        role="observation",
        scope="node",
        payload={
            "ok": ok,
            "required": list(required),
            "observed": list(observed),
            "missing": list(missing),
            "context_found": context_found,
            "skills_heading_found": skills_heading_found,
            "error": str(error or ""),
            "execution_id": str(execution_id or ""),
            "satellite_execution_id": str(satellite_execution_id or ""),
        },
    )


def emit_skill_context_loaded(
    *,
    ok: bool,
    required: list[str],
    observed: list[str],
    missing: list[str],
    execution_id: str = "",
    satellite_execution_id: str = "",
    context_found: bool = False,
    skills_heading_found: bool = False,
    error: str = "",
) -> Event | None:
    """Best-effort ``cdp.skill.context_loaded``; never raises."""
    try:
        event = cdp_skill_context_loaded(
            ok=ok,
            required=required,
            observed=observed,
            missing=missing,
            execution_id=execution_id,
            satellite_execution_id=satellite_execution_id,
            context_found=context_found,
            skills_heading_found=skills_heading_found,
            error=error,
        )
    except Exception:  # noqa: BLE001 — receipt must not fail submit
        return None
    _mirror_to_event_service(event)
    return event


@event_factory
def cdp_skill_fetch_decision(
    *,
    ref: str,
    decision: str,
    reason: str = "",
    required: list[str] | None = None,
    observed: list[str] | None = None,
    sha256: str = "",
    nbytes: int = 0,
) -> Event:
    """Staged-fetch decision. A skip is a row, not an absent event.

    ``decision`` is ``in_context``, ``resolved``, or ``skipped``. ``resolved``
    hashes bytes the harness held. ``in_context`` is an attestation that the
    body was loaded. Neither is a seat self-report.
    """
    return Event(
        signal="cdp.skill.fetch_decision",
        role="observation",
        scope="node",
        payload={
            "ref": str(ref),
            "decision": str(decision),
            "reason": str(reason or ""),
            "required": list(required or []),
            "observed": list(observed or []),
            "sha256": str(sha256 or ""),
            "nbytes": int(nbytes),
        },
    )


def emit_skill_fetch_decision(
    *,
    ref: str,
    decision: str,
    reason: str = "",
    required: list[str] | None = None,
    observed: list[str] | None = None,
    sha256: str = "",
    nbytes: int = 0,
) -> Event | None:
    """Best-effort ``cdp.skill.fetch_decision``; never raises."""
    if decision not in {"in_context", "resolved", "skipped"}:
        return None
    try:
        event = cdp_skill_fetch_decision(
            ref=ref,
            decision=decision,
            reason=reason,
            required=required,
            observed=observed,
            sha256=sha256,
            nbytes=nbytes,
        )
    except Exception:  # noqa: BLE001 — receipt must not fail the panel
        return None
    _mirror_to_event_service(event)
    return event


def _ensure_mirror_worker() -> None:
    global _mirror_worker_started  # noqa: PLW0603
    with _mirror_worker_lock:
        if _mirror_worker_started:
            return
        thread = threading.Thread(
            target=_mirror_worker_loop,
            name="cdp-skill-delivery-mirror",
            daemon=True,
        )
        thread.start()
        _mirror_worker_started = True


def _record_drop(*, transport: str, target: str, circuit: bool) -> None:
    global _circuit_skip_count, _queue_drop_count  # noqa: PLW0603
    key = (transport, target)
    with _mirror_state_lock:
        if circuit:
            _circuit_skip_count += 1
        else:
            _queue_drop_count += 1
        fails, drops = _warning_counts.get(key, (0, 0))
        _warning_counts[key] = (fails, drops + 1)


def _record_send_failure(transport: str, target: str, exc: Exception) -> None:
    global _circuit_open_until  # noqa: PLW0603
    key = (transport, target)
    now = time.monotonic()
    should_warn = False
    with _mirror_state_lock:
        _circuit_open_until = now + _CIRCUIT_BREAKER_SECONDS
        fails, drops = _warning_counts.get(key, (0, 0))
        _warning_counts[key] = (fails + 1, drops)
        last = _last_warning_at.get(key, 0.0)
        if now - last >= _WARNING_INTERVAL_SECONDS:
            should_warn = True
    if should_warn:
        _emit_mirror_warning(key, transport, target, exc)


def _emit_mirror_warning(
    key: tuple[str, str],
    transport: str,
    target: str,
    exc: Exception,
) -> None:
    with _mirror_state_lock:
        fails, drops = _warning_counts.get(key, (0, 0))
        _warning_counts[key] = (0, 0)
        _last_warning_at[key] = time.monotonic()
    logger.warning(
        "cdp-skill-delivery Event Service mirror failed "
        "(transport=%s target=%s failures=%s drops=%s): %s",
        transport,
        target,
        fails,
        drops,
        exc,
    )


def _mirror_send_sync(event: Event) -> None:
    """TCP when configured, else UDS; raises on connect/send failure."""
    payload: dict[str, Any] = {
        "signal": event.signal,
        "source": "cdp-skill-delivery",
        "role": event.role,
        "scope": event.scope,
        "ts_unix_ms": int(time.time() * 1000),
        "payload": event.payload,
    }
    line = (json.dumps(payload) + "\n").encode()
    tcp = _resolve_tcp_target()
    if tcp is not None:
        host, port = tcp
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect((host, port))
            sock.sendall(line)
        return
    sock_path = os.environ.get(
        "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
    )
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        sock.connect(sock_path)
        sock.sendall(line)


def _mirror_worker_loop() -> None:
    while True:
        item = _mirror_queue.get()
        try:
            if isinstance(item, _MirrorFlush):
                item.done.set()
                continue
            event = item
            transport = ""
            target = ""
            try:
                tcp = _resolve_tcp_target()
                if tcp is not None:
                    host, port = tcp
                    transport = "tcp"
                    target = f"{host}:{port}"
                else:
                    transport = "uds"
                    target = os.environ.get(
                        "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
                    )
                _mirror_send_sync(event)
            except Exception as exc:  # noqa: BLE001 — worker must not die
                _record_send_failure(transport, target, exc)
        except Exception:  # noqa: BLE001 — never escape the worker thread
            pass
        finally:
            _mirror_queue.task_done()


def _mirror_to_event_service(event: Event) -> None:
    """Best-effort Event Service ingest — enqueue only; never blocks on socket I/O."""
    now = time.monotonic()
    transport = ""
    target = ""
    tcp = _resolve_tcp_target()
    if tcp is not None:
        host, port = tcp
        transport = "tcp"
        target = f"{host}:{port}"
    else:
        transport = "uds"
        target = os.environ.get(
            "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
        )
    with _mirror_state_lock:
        circuit_open = now < _circuit_open_until
    if circuit_open:
        _record_drop(transport=transport, target=target, circuit=True)
        return
    try:
        _mirror_queue.put_nowait(event)
    except queue.Full:
        _record_drop(transport=transport, target=target, circuit=False)
        return
    _ensure_mirror_worker()


def test_flush_mirror_worker(*, timeout: float = 5.0) -> None:
    """Test-only: block until queued mirror work through this point is drained."""
    flush = _MirrorFlush()
    try:
        _mirror_queue.put_nowait(flush)
    except queue.Full:
        pass
    _ensure_mirror_worker()
    flush.done.wait(timeout=timeout)


def test_reset_mirror_transport_state() -> None:
    """Test-only: clear breaker, counters, and warning throttle keys."""
    global _circuit_open_until, _circuit_skip_count, _queue_drop_count  # noqa: PLW0603
    with _mirror_state_lock:
        _circuit_open_until = 0.0
        _circuit_skip_count = 0
        _queue_drop_count = 0
        _last_warning_at.clear()
        _warning_counts.clear()
    while True:
        try:
            _mirror_queue.get_nowait()
        except queue.Empty:
            break
