"""JSONL replay source -- the stdlib stand-in for the G5 ``UlgEventSource``.

Satisfies :class:`~dispatch_monitor_core.protocols.EventSource` over a JSONL file
or an iterable of lines, so the Model is exercisable with zero adapters and the
fixture suite is the same code path the graft will replace.

This module and ``__main__`` are the **only** two places in the core that touch a
filesystem, and they touch nothing else -- no socket, no bus, no network. The Model
still never does I/O: it receives records that this source produced.

Line format, one JSON object per line::

    {"signal": "...", "ts_unix_ms": 0, "seq": 1, "payload": {...},
     "source": "ulg://charter-runner", "subject": "5735", "id": "..."}

``signal`` and ``ts_unix_ms`` are required. ``seq`` / ``source`` / ``subject`` /
``id`` are optional; blank lines and ``#`` comments are skipped so fixtures can
carry section headers.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from .protocols import Event, EventRecord


def parse_line(line: str) -> Event | None:
    """Parse one JSONL line into an :class:`Event`, or ``None`` if it is not a record.

    Raises ``ValueError`` on a line that is JSON but not a usable record, so a
    malformed fixture fails loudly at load rather than silently folding nothing.
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"replay line is not an object: {text[:80]}")
    if "signal" not in data or "ts_unix_ms" not in data:
        raise ValueError(f"replay line lacks signal/ts_unix_ms: {text[:80]}")
    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError(f"replay payload is not an object: {text[:80]}")
    return Event(
        signal=str(data["signal"]),
        ts_unix_ms=int(data["ts_unix_ms"]),
        payload=payload,
        seq=data.get("seq"),
        source=data.get("source"),
        subject=data.get("subject"),
        id=data.get("id"),
    )


def parse_lines(lines: Iterable[str]) -> Iterator[Event]:
    """Yield every :class:`Event` in ``lines``, skipping blanks and comments."""
    for line in lines:
        event = parse_line(line)
        if event is not None:
            yield event


def load_fixture(path: str) -> tuple[Event, ...]:
    """Read a JSONL fixture from ``path`` and return its records in file order."""
    with open(path, encoding="utf-8") as handle:
        return tuple(parse_lines(handle))


class JsonlEventSource:
    """An :class:`EventSource` backed by a JSONL fixture.

    Deliberately synchronous and eager: ``subscribe`` drains the whole fixture
    through the handler and returns. Replay determinism is the point -- there is no
    reconnect, no ``resume_from``, and no concurrency to reason about, so a failing
    fold test has exactly one possible cause.
    """

    def __init__(self, records: Iterable[EventRecord]) -> None:
        self._records = tuple(records)

    @classmethod
    def from_path(cls, path: str) -> JsonlEventSource:
        """Build a source from a JSONL fixture file."""
        return cls(load_fixture(path))

    @property
    def records(self) -> tuple[EventRecord, ...]:
        """Return the loaded records in file order."""
        return self._records

    def subscribe(self, handler: Callable[[EventRecord], None]) -> None:
        """Feed every record to ``handler`` in order, then return."""
        for record in self._records:
            handler(record)

    def max_ts(self) -> int:
        """Return the latest ``ts_unix_ms`` in the fixture, or ``0`` if empty.

        Useful as a deterministic ``now_ms`` for replay: deriving at the fixture's
        own high-water timestamp keeps age-derived fields reproducible across runs.
        """
        return max((r.ts_unix_ms for r in self._records), default=0)


def default_clock_from(records: Iterable[EventRecord]) -> Callable[[], int]:
    """Return a frozen clock pinned to the newest timestamp in ``records``.

    A frozen clock is what makes replay assertions stable: with a real clock every
    ``*_age_ms`` field drifts between runs and no fixture test can assert on them.
    """
    high_water = max((r.ts_unix_ms for r in records), default=0)

    def _now() -> int:
        return high_water

    return _now


def as_records(rows: Iterable[dict[str, Any]]) -> tuple[Event, ...]:
    """Build records from already-decoded dicts, for tests that skip the file."""
    return tuple(parse_lines(json.dumps(row) for row in rows))
