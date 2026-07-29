"""WakeHub — Event Service subscribe + coalescing dirty-set for charter-runner M2."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from universal_logging import get_logger

from libs.charter_runner_store.db import execute_with_retry

from .admission import CapStore
from .root_ledger import open_default_ledger

logger = get_logger(__name__)

RESUME_SEQ_KEY = "wake_hub_resume_seq"
_SUBSCRIBE_URL = "http://localhost/v1/subscribe"
_DEFAULT_BACKOFF_S = 2.0
_META_TYPES = frozenset({"subscribed", "events.dropped.subscribe"})

# §B3 wake-signal union (subscribe-time glob per signal family).
B3_SIGNAL_FILTERS: tuple[dict[str, str], ...] = (
    {"signal": "mcp.agentbus.turn.created"},
    {"signal": "mcp.agentbus.thread.lifecycle.transitioned"},
    {"signal": "frontier.sdk.worker.*"},
    {"signal": "manage.charter.tick.resumed"},
    {"signal": "manage.charter.caps.cleared"},
)

SubscribeFactory = Callable[[dict[str, str], int], AsyncIterator[dict[str, Any]]]
ListEnrolledRoots = Callable[[], Awaitable[list[dict[str, Any]]]]
OnWake = Callable[[str, str, int], Awaitable[None]]
OnFullRosterWake = Callable[[], Awaitable[None]]


def _field(ev: dict[str, Any], key: str) -> Any:
    if key in ev:
        return ev[key]
    payload = ev.get("payload")
    if isinstance(payload, dict):
        return payload.get(key)
    return None


def read_resume_seq(*, conn=None) -> int:
    """Load persisted subscribe cursor from ``ledger_meta``."""
    owned = conn is None
    if owned:
        conn = open_default_ledger()
    try:
        row = conn.execute(
            "SELECT value FROM ledger_meta WHERE key = ?", (RESUME_SEQ_KEY,)
        ).fetchone()
        if row is None:
            return 0
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return 0
    finally:
        if owned:
            conn.close()


def write_resume_seq(seq: int, *, conn=None) -> None:
    """Persist subscribe cursor — sole writer: WakeHub."""
    owned = conn is None
    if owned:
        conn = open_default_ledger()
    try:
        execute_with_retry(
            conn,
            """
            INSERT INTO ledger_meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (RESUME_SEQ_KEY, str(int(seq))),
        )
    finally:
        if owned:
            conn.close()


@dataclass
class WakeDirtySet:
    """In-process coalescing dirty set — single consumer drains."""

    _roots: set[str] = field(default_factory=set)
    _pending: dict[str, int] = field(default_factory=dict)
    _event: asyncio.Event = field(default_factory=asyncio.Event)

    def enqueue(self, root_id: str, *, coalesce: bool = True) -> int:
        """Mark ``root_id`` dirty; return coalesced event count for telemetry."""
        root_id = str(root_id or "").strip()
        if not root_id:
            return 0
        self._roots.add(root_id)
        if coalesce:
            self._pending[root_id] = self._pending.get(root_id, 0) + 1
        else:
            self._pending.setdefault(root_id, 1)
        self._event.set()
        return self._pending[root_id]

    def enqueue_many(self, root_ids: set[str]) -> None:
        for root_id in root_ids:
            if root_id:
                self.enqueue(root_id, coalesce=False)

    async def wait(self, timeout: float | None) -> bool:
        """Return True when the dirty event fired before timeout."""
        if self._roots:
            return True
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    def drain(self) -> list[tuple[str, int]]:
        """Copy+clear under single-consumer discipline (no lock)."""
        if not self._roots:
            self._event.clear()
            return []
        batch = [(root, self._pending.get(root, 1)) for root in sorted(self._roots)]
        self._roots.clear()
        self._pending.clear()
        self._event.clear()
        return batch


class WakeRootMapper:
    """Map subscribed signals to enrolled root ids (roster filter at mapper)."""

    def __init__(self, list_enrolled_roots: ListEnrolledRoots) -> None:
        self._list_enrolled_roots = list_enrolled_roots
        self._enrolled: set[str] = set()

    @property
    def enrolled(self) -> set[str]:
        return set(self._enrolled)

    async def refresh_enrolled(self) -> set[str]:
        roots = await self._list_enrolled_roots()
        self._enrolled = {
            str(thread.get("id") or "") for thread in roots if thread.get("id")
        }
        return set(self._enrolled)

    def map_event(self, ev: dict[str, Any], *, caps: CapStore | None = None) -> str | None:
        signal = str(_field(ev, "signal") or ev.get("signal") or "")
        if signal == "manage.charter.tick.resumed":
            return None
        if signal == "manage.charter.caps.cleared":
            root = str(_field(ev, "root") or "").strip()
            return root if root in self._enrolled else None
        if signal in (
            "mcp.agentbus.turn.created",
            "mcp.agentbus.thread.lifecycle.transitioned",
        ):
            thread = str(_field(ev, "thread") or "").strip()
            return thread if thread in self._enrolled else None
        if signal.startswith("frontier.sdk.worker."):
            thread_id = str(_field(ev, "thread_id") or "").strip()
            if not thread_id or caps is None:
                return None
            return lookup_root_for_worker_thread(thread_id, caps)
        return None


def lookup_root_for_worker_thread(thread_id: str, caps: CapStore) -> str | None:
    """Resolve worker bus thread → charter root via intent bind or ledger wip."""
    conn = open_default_ledger()
    try:
        row = conn.execute(
            "SELECT root_id FROM root_ledger WHERE wip_window_id = ?",
            (thread_id,),
        ).fetchone()
        if row is not None:
            return str(row[0])
    finally:
        conn.close()
    intent_dir = caps._intent_dir
    if not intent_dir.is_dir():
        return None
    for path in intent_dir.glob("*.intent"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines[1:]:
            if line.startswith("worker=") and line.split("=", 1)[1].strip() == thread_id:
                stem = path.stem
                if "-w" in stem:
                    return stem.rsplit("-w", 1)[0]
                return None
    return None


async def _aclose(agen: AsyncIterator[dict[str, Any]]) -> None:
    aclose = getattr(agen, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except Exception:  # pragma: no cover — best-effort cleanup
            logger.debug("wake subscription aclose failed", exc_info=True)


@dataclass
class WakeHub:
    """One subscribe hub: B3 filters, resume seq, dirty-set enqueue."""

    dirty: WakeDirtySet
    mapper: WakeRootMapper
    caps: CapStore
    subscribe_events: SubscribeFactory
    on_wake: OnWake
    on_full_roster_wake: OnFullRosterWake
    backoff_s: float = _DEFAULT_BACKOFF_S
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)
    _resume_seq: int = 0

    async def start(self) -> None:
        if self._tasks:
            return
        self._resume_seq = read_resume_seq()
        await self.mapper.refresh_enrolled()
        for filt in B3_SIGNAL_FILTERS:
            self._tasks.append(asyncio.create_task(self._subscribe_loop(filt)))

    async def stop(self) -> None:
        tasks = list(self._tasks)
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _subscribe_loop(self, event_filter: dict[str, str]) -> None:
        while True:
            try:
                await self._consume_filter(event_filter)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — degrade to floor-only
                logger.warning(
                    "wake subscribe unavailable filter=%s; retrying",
                    event_filter,
                    exc_info=True,
                )
            await asyncio.sleep(self.backoff_s)

    async def _consume_filter(self, event_filter: dict[str, str]) -> None:
        agen = self.subscribe_events(event_filter, self._resume_seq)
        try:
            async for ev in agen:
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") in _META_TYPES:
                    continue
                seq = _field(ev, "seq")
                if isinstance(seq, int) and seq > self._resume_seq:
                    self._resume_seq = seq
                    write_resume_seq(self._resume_seq)
                await self._handle_event(ev)
        finally:
            await _aclose(agen)

    async def _handle_event(self, ev: dict[str, Any]) -> None:
        signal = str(_field(ev, "signal") or ev.get("signal") or "")
        if signal == "manage.charter.tick.resumed":
            await self.mapper.refresh_enrolled()
            await self.on_full_roster_wake()
            return
        root_id = self.mapper.map_event(ev, caps=self.caps)
        if not root_id:
            return
        coalesced_n = self.dirty.enqueue(root_id)
        await self.on_wake(root_id, signal, coalesced_n)


def default_events_query_socket() -> str:
    return os.environ.get(
        "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
    )


def build_wake_subscribe_factory(
    events_query_socket: str | None = None,
) -> SubscribeFactory:
    """Wire live Event Service WS subscribe (GIW transport shape)."""

    sock = events_query_socket or default_events_query_socket()

    def _factory(
        event_filter: dict[str, str], resume_seq: int
    ) -> AsyncIterator[dict[str, Any]]:
        return _live_subscribe(sock, event_filter, resume_seq)

    return _factory


async def _live_subscribe(
    events_query_socket: str,
    event_filter: dict[str, str],
    resume_seq: int,
) -> AsyncIterator[dict[str, Any]]:
    import aiohttp

    connector = aiohttp.UnixConnector(path=events_query_socket)
    async with (
        aiohttp.ClientSession(connector=connector) as session,
        session.ws_connect(_SUBSCRIBE_URL) as ws,
    ):
        await ws.send_json(
            {
                "type": "subscribe",
                "filter": event_filter,
                "resume_from": {"seq": resume_seq},
            }
        )
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict) and data.get("type") not in _META_TYPES:
                    yield data
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break


__all__ = [
    "B3_SIGNAL_FILTERS",
    "RESUME_SEQ_KEY",
    "WakeDirtySet",
    "WakeHub",
    "WakeRootMapper",
    "build_wake_subscribe_factory",
    "lookup_root_for_worker_thread",
    "read_resume_seq",
    "write_resume_seq",
]
