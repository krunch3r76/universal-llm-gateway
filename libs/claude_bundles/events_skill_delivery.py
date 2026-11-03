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

_now = time.monotonic

_MIRROR_QUEUE_MAXSIZE = 256
_CIRCUIT_BREAKER_SECONDS = 30.0
_WARNING_INTERVAL_SECONDS = 300.0

_mirror_queue: queue.Queue[Any] = queue.Queue(maxsize=_MIRROR_QUEUE_MAXSIZE)
_mirror_worker_lock = threading.Lock()
_mirror_worker_started = False
_mirror_worker_thread: threading.Thread | None = None
_mirror_worker_owner_pid = 0
_MIRROR_WORKER_NAME = "cdp-skill-delivery-mirror"
_circuit_open_until = 0.0
_mirror_state_lock = threading.Lock()
# (transport, target) -> monotonic last warning time
_last_warning_at: dict[tuple[str, str], float] = {}
# (transport, target) -> (failures, drops) since last warning
_warning_counts: dict[tuple[str, str], tuple[int, int]] = {}
_circuit_skip_count = 0
_queue_drop_count = 0
_internal_error_count = 0

_getpid = os.getpid


def _start_mirror_thread(thread: threading.Thread) -> None:
    thread.start()


class _MirrorFlush:
    """Test-only queue sentinel; worker sets ``done`` after draining ahead of it."""

    __slots__ = ("done",)

    def __init__(self) -> None:
        self.done = threading.Event()


class _MirrorShutdown:
    """Test-only sentinel; worker exits after draining ahead of it."""


class _MirrorSerializationNotice(Exception):
    """Synthetic exception for non-serializable mirror payloads."""


def _reset_mirror_after_fork() -> None:
    """Reinitialize mirror globals in a forked child (N2)."""
    global _mirror_queue  # noqa: PLW0603
    global _mirror_worker_lock, _mirror_state_lock  # noqa: PLW0603
    global _mirror_worker_started, _mirror_worker_thread, _mirror_worker_owner_pid
    global _circuit_open_until, _last_warning_at, _warning_counts
    global _circuit_skip_count, _queue_drop_count, _internal_error_count
    _mirror_queue = queue.Queue(maxsize=_MIRROR_QUEUE_MAXSIZE)
    _mirror_worker_lock = threading.Lock()
    _mirror_state_lock = threading.Lock()
    _mirror_worker_started = False
    _mirror_worker_thread = None
    _mirror_worker_owner_pid = _getpid()
    _circuit_open_until = 0.0
    _last_warning_at = {}
    _warning_counts = {}
    _circuit_skip_count = 0
    _queue_drop_count = 0
    _internal_error_count = 0


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_mirror_after_fork)


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


class _MirrorDropNotice(Exception):
    """Synthetic exception for drop-only mirror warnings."""


def _mirror_transport_target() -> tuple[str, str]:
    try:
        tcp = _resolve_tcp_target()
        if tcp is not None:
            host, port = tcp
            return "tcp", f"{host}:{port}"
    except Exception:  # noqa: BLE001 — fall through to UDS label
        pass
    return "uds", os.environ.get(
        "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
    )


def _mirror_worker_stale() -> bool:
    if not _mirror_worker_started:
        return False
    if _getpid() != _mirror_worker_owner_pid:
        return True
    return _mirror_worker_thread is None or not _mirror_worker_thread.is_alive()


def _ensure_mirror_worker() -> None:
    global _mirror_worker_started, _mirror_worker_thread, _mirror_worker_owner_pid  # noqa: PLW0603
    with _mirror_worker_lock:
        if _mirror_worker_stale():
            _mirror_worker_started = False
            _mirror_worker_thread = None
        if _mirror_worker_started:
            return
        thread = threading.Thread(
            target=_mirror_worker_loop,
            name=_MIRROR_WORKER_NAME,
            daemon=True,
        )
        try:
            _start_mirror_thread(thread)
        except Exception as exc:  # noqa: BLE001 — mirror must not raise into emit_*
            transport, target = _mirror_transport_target()
            _record_internal_error(transport, target, exc)
            return
        _mirror_worker_thread = thread
        _mirror_worker_owner_pid = _getpid()
        _mirror_worker_started = True


def _emit_mirror_warning_if_due(
    key: tuple[str, str],
    transport: str,
    target: str,
    exc: Exception,
) -> None:
    global _internal_error_count  # noqa: PLW0603
    now = _now()
    with _mirror_state_lock:
        last = _last_warning_at.get(key, 0.0)
        if now - last < _WARNING_INTERVAL_SECONDS:
            return
        fails, drops = _warning_counts.get(key, (0, 0))
        _warning_counts[key] = (0, 0)
        _last_warning_at[key] = now
        internal = _internal_error_count
        _internal_error_count = 0
    logger.warning(
        "cdp-skill-delivery Event Service mirror failed "
        "(transport=%s target=%s failures=%s drops=%s internal_errors=%s): %s",
        transport,
        target,
        fails,
        drops,
        internal,
        exc,
    )


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
    _emit_mirror_warning_if_due(
        key, transport, target, _MirrorDropNotice("mirror dropped")
    )


def _record_internal_error(transport: str, target: str, exc: Exception) -> None:
    global _internal_error_count  # noqa: PLW0603
    key = (transport, target)
    with _mirror_state_lock:
        _internal_error_count += 1
        fails, drops = _warning_counts.get(key, (0, 0))
        _warning_counts[key] = (fails + 1, drops)
    _emit_mirror_warning_if_due(key, transport, target, exc)


def _record_serialization_drop(transport: str, target: str, exc: Exception) -> None:
    """Non-serializable payload — drop only; must not open the circuit breaker."""
    global _queue_drop_count  # noqa: PLW0603
    key = (transport, target)
    with _mirror_state_lock:
        _queue_drop_count += 1
        fails, drops = _warning_counts.get(key, (0, 0))
        _warning_counts[key] = (fails, drops + 1)
    _emit_mirror_warning_if_due(
        key, transport, target, _MirrorSerializationNotice(str(exc))
    )


def _record_send_failure(transport: str, target: str, exc: Exception) -> None:
    global _circuit_open_until  # noqa: PLW0603
    key = (transport, target)
    now = _now()
    with _mirror_state_lock:
        _circuit_open_until = now + _CIRCUIT_BREAKER_SECONDS
        fails, drops = _warning_counts.get(key, (0, 0))
        _warning_counts[key] = (fails + 1, drops)
    _emit_mirror_warning_if_due(key, transport, target, exc)


def _serialize_mirror_line(event: Event) -> bytes:
    payload: dict[str, Any] = {
        "signal": event.signal,
        "source": "cdp-skill-delivery",
        "role": event.role,
        "scope": event.scope,
        "ts_unix_ms": int(time.time() * 1000),
        "payload": event.payload,
    }
    return (json.dumps(payload) + "\n").encode()


def _mirror_send_sync(line: bytes) -> None:
    """TCP when configured, else UDS; raises on connect/send failure."""
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
    global _mirror_worker_started, _mirror_worker_thread  # noqa: PLW0603
    try:
        while True:
            item = _mirror_queue.get()
            try:
                if isinstance(item, _MirrorFlush):
                    item.done.set()
                    continue
                if isinstance(item, _MirrorShutdown):
                    break
                line = item
                transport, target = _mirror_transport_target()
                if _now() < _circuit_open_until:
                    _record_drop(transport=transport, target=target, circuit=True)
                    continue
                try:
                    _mirror_send_sync(line)
                except Exception as exc:  # noqa: BLE001 — worker must not die
                    _record_send_failure(transport, target, exc)
            except Exception as exc:  # noqa: BLE001 — never escape the worker thread
                transport, target = _mirror_transport_target()
                _record_internal_error(transport, target, exc)
            finally:
                _mirror_queue.task_done()
    finally:
        with _mirror_worker_lock:
            if threading.current_thread() is _mirror_worker_thread:
                _mirror_worker_started = False
                _mirror_worker_thread = None


def _mirror_to_event_service(event: Event) -> None:
    """Best-effort Event Service ingest — enqueue only; never blocks on socket I/O."""
    try:
        now = _now()
        transport, target = _mirror_transport_target()
        with _mirror_state_lock:
            circuit_open = now < _circuit_open_until
        if circuit_open:
            _record_drop(transport=transport, target=target, circuit=True)
            return
        try:
            line = _serialize_mirror_line(event)
        except (TypeError, ValueError) as exc:
            _record_serialization_drop(transport, target, exc)
            return
        try:
            _mirror_queue.put_nowait(line)
        except queue.Full:
            _record_drop(transport=transport, target=target, circuit=False)
            return
        _ensure_mirror_worker()
    except Exception as exc:  # noqa: BLE001 — mirror must not raise into emit_*
        try:
            transport, target = _mirror_transport_target()
        except Exception:  # noqa: BLE001 — still record the original fault
            transport, target = "uds", "unknown"
        _record_internal_error(transport, target, exc)


def _live_mirror_workers() -> list[threading.Thread]:
    """Return mirror threads still alive, including ones replaced after a pid change.

    ``_mirror_worker_thread`` only points at the latest start. An older thread
    stays blocked in ``Queue.get`` on the same queue and will consume a single
    shutdown that was meant for the thread ``reset`` joins.
    """
    found = [
        thread
        for thread in threading.enumerate()
        if thread.name == _MIRROR_WORKER_NAME and thread.is_alive()
    ]
    current = _mirror_worker_thread
    if current is not None and current.is_alive() and current not in found:
        found.append(current)
    return found


def _enqueue_mirror_sentinel(item: _MirrorFlush | _MirrorShutdown) -> bool:
    """Queue a flush or shutdown marker, dropping one payload line if full.

    ``put_nowait`` raising ``queue.Full`` used to be swallowed. The caller then
    waited out ``timeout`` because the worker never observed the marker.
    Returns False only when the marker itself could not be queued.
    """
    for _ in range(_MIRROR_QUEUE_MAXSIZE + 1):
        try:
            _mirror_queue.put_nowait(item)
            return True
        except queue.Full:
            try:
                evicted = _mirror_queue.get_nowait()
            except queue.Empty:
                continue
            if isinstance(evicted, (_MirrorFlush, _MirrorShutdown)):
                try:
                    _mirror_queue.put_nowait(evicted)
                except queue.Full:
                    return False
            else:
                try:
                    _mirror_queue.task_done()
                except ValueError:
                    pass
    return False


def flush_mirror_worker_for_tests(*, timeout: float = 5.0) -> None:
    """Block until mirror work queued before this call has been processed.

    Test-only. The flush marker is placed even when the bounded queue holds
    only payload lines, so the wait ends because the worker set ``done``.
    When no mirror thread is alive (worker start refused), the marker cannot
    be observed and this returns instead of burning ``timeout``. ``timeout``
    remains an upper bound and is not the success path.
    """
    flush = _MirrorFlush()
    queued = _enqueue_mirror_sentinel(flush)
    _ensure_mirror_worker()
    if queued and _live_mirror_workers():
        flush.done.wait(timeout=timeout)


def reset_mirror_state_for_tests() -> None:
    """Stop every live mirror worker, then clear breaker, counters, and throttle.

    Test-only. One shutdown wakes one blocked worker; a thread left behind
    after a pid change takes that shutdown and the joined thread sits in
    ``join`` until timeout. Each live worker gets its own shutdown. The worker
    lock is not held across ``join``.
    """
    global _circuit_open_until, _circuit_skip_count, _queue_drop_count  # noqa: PLW0603
    global _mirror_worker_started, _mirror_worker_thread, _mirror_worker_owner_pid
    global _internal_error_count
    with _mirror_worker_lock:
        workers = _live_mirror_workers()
        _mirror_worker_started = False
        _mirror_worker_thread = None
        _mirror_worker_owner_pid = 0
    for _worker in workers:
        _enqueue_mirror_sentinel(_MirrorShutdown())
    for worker in workers:
        worker.join(timeout=5.0)
    with _mirror_state_lock:
        _circuit_open_until = 0.0
        _circuit_skip_count = 0
        _queue_drop_count = 0
        _internal_error_count = 0
        _last_warning_at.clear()
        _warning_counts.clear()
    while True:
        try:
            _mirror_queue.get_nowait()
        except queue.Empty:
            break
