"""Structural contracts between the portable core and its ULG graft adapters.

The core never imports a transport, a socket, or a repo module. Everything the
Model needs from the outside world arrives as an :class:`EventRecord`; everything
the Controller is expected to supply is declared here as a ``Protocol`` so the
G5 graft can satisfy it without the core knowing how.

Envelope note (Fable G3 §3.4): the live ULG ``SignalEnvelope`` carries
``signal`` / ``ts_unix_ms`` / ``seq`` / ``payload`` today. Fable recommends adding
CloudEvents ``id`` / ``source`` / ``subject``. :class:`EventRecord` requires only
the four fields that exist now; the three recommended additions are read through
:func:`envelope_source`, :func:`envelope_subject` and :func:`envelope_id`, which
degrade to ``None`` when absent. A pre-G5 four-field record is therefore a valid
``EventRecord``, and the core gains attribution automatically once the additions
land -- no core change at the seam.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EventRecord(Protocol):
    """One folded input, as seen by the Model.

    Only the four fields the live envelope already guarantees are required.
    ``seq`` is Event-Service-assigned and is ``None`` on projection-role
    envelopes, which the Model never folds.
    """

    signal: str
    ts_unix_ms: int
    seq: int | None
    payload: Mapping[str, Any]


class EventSource(Protocol):
    """Controller-owned ingress. The core declares it and never constructs one."""

    def subscribe(self, handler: Callable[[EventRecord], None]) -> None:
        """Register ``handler`` for every record this source yields."""
        ...


class Clock(Protocol):
    """Injected time. The Model reads the clock only through ``derive(now_ms)``."""

    def now_ms(self) -> int:
        """Return the current wall time in Unix milliseconds."""
        ...


class ReconcilePort(Protocol):
    """Click-time, operator-initiated reconciliation against a live surface.

    Deliberately narrow. This port exists so a View click can ask "what does the
    bus say about this thread right now"; it is **not** a steady-state input.
    Nothing in :class:`~dispatch_monitor_core.model.Model` calls it, and nothing
    it returns enters the fold -- any datum the View needs continuously must
    arrive as an :class:`EventRecord` instead (Fable G3 §3.1 invariant).
    """

    def reconcile(self, kind: str, key: str) -> Mapping[str, Any]:
        """Fetch a point-in-time detail record for ``key`` of class ``kind``."""
        ...


class DetailPort(Protocol):
    """Read-only detail fetch for display; never a fold input.

    Like :class:`ReconcilePort`, this port is operator-initiated and
    click-time. Nothing it returns enters the fold -- any datum the View
    needs continuously must arrive as an :class:`EventRecord` instead.
    """

    def fetch(self, kind: str, key: str) -> Mapping[str, Any]:
        """Return a mapping suitable for display; never raises.

        The returned mapping is for rendering only and does not enter the
        fold. On ``kind="prompt"`` the mapping includes ``kind``, ``key``,
        ``source``, ``body``, ``bytes``, ``truncated``, and ``error``. For
        any other ``kind`` the mapping is ``kind``, ``key``, and
        ``error`` set to ``"unsupported_kind"``.
        """
        ...


class TailPort(Protocol):
    """Click-time tail of one selected dispatch. Never a fold input.

    The view polls ``tail`` and holds only the cursor. Nothing returned
    enters the fold, and the call publishes no snapshot and no event.
    """

    def tail(self, kind: str, key: str, cursor: int) -> Mapping[str, Any]:
        """Return ``lines``, ``cursor``, ``eof``, and ``source``. Never raises.

        ``kind`` selects the substrate. ``key`` is that substrate's id.
        ``cursor`` is the last line the caller has already shown; the
        mapping's ``cursor`` is the new one. ``eof`` is true when no later
        line will arrive. On an unsupported ``kind`` the mapping includes
        ``error`` set to ``"unsupported_kind"``.
        """
        ...


class ControllerPort(Protocol):
    """The lifecycle the G5 Controller is expected to implement.

    Described, not wired. Present so the graft has a named target and so the
    ``--watch`` harness in ``__main__`` can be read as a degenerate Controller.
    """

    def start(self) -> None:
        """Open ingress, seed cold-start state, begin the tick."""
        ...

    def tick(self) -> None:
        """Derive once and publish the frame if its fingerprint changed."""
        ...

    def stop(self) -> None:
        """Close ingress and the projection hub."""
        ...


@dataclass(frozen=True)
class Event:
    """Concrete :class:`EventRecord` for fixtures, tests and the watch harness.

    The graft supplies its own record type from ``libs/event_store``; this one
    exists so the core is exercisable with zero adapters.
    """

    signal: str
    ts_unix_ms: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    seq: int | None = None
    source: str | None = None
    subject: str | None = None
    id: str | None = None


def envelope_source(record: EventRecord) -> str | None:
    """Return the CloudEvents ``source`` of ``record``, or ``None`` if unset.

    Producer identity disambiguates the GS2 dual-emitter case: the same
    cursor-sdk terminal is observable from both the worker lane and the pipeline
    lane, and ``source`` is what names which one spoke.
    """
    value = getattr(record, "source", None)
    return value if isinstance(value, str) and value else None


def envelope_subject(record: EventRecord) -> str | None:
    """Return the CloudEvents ``subject`` of ``record``, or ``None`` if unset.

    When present this is the correlation key (thread id / root id / dispatch id)
    carried in envelope context rather than dug out of ``payload``.
    """
    value = getattr(record, "subject", None)
    return value if isinstance(value, str) and value else None


def envelope_id(record: EventRecord) -> str | None:
    """Return the CloudEvents ``id`` of ``record``, or ``None`` if unset.

    ``(source, id)`` is the pair that makes a reconnect + ``resume_from`` overlap
    window de-duplicable. The core records it for idempotence bookkeeping only.
    """
    value = getattr(record, "id", None)
    return value if isinstance(value, str) and value else None
