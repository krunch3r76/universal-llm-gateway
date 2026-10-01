"""One corpus-hint rebuild at a time, with waiters collapsed into one rerun.

Off-thread scans can overlap. A slower scan that started earlier can commit
after a newer one and put stale hints back. The gate holds a single runner.
Callers that arrive while it runs set a dirty flag and wait; their scope sets
are unioned so the runner does one follow-up instead of one scan each.

Requests merge only when every field outside the scope set is equal and both
use the same scan mode (scope column, or source prefixes). A cancelled runner
does not publish that cancellation to waiters: it drops the running flag,
leaves the pending union in place, and a waiter runs it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path

_gates: dict[str, HintRebuildGate] = {}


@dataclass
class HintRebuildRequest:
    """Arguments for one hint rebuild.

    ``scope`` and ``configured_scopes`` are the only fields a merge may change.
    Callers whose other fields differ run one after another.
    """

    scope: str | None
    configured_scopes: dict[str, list[str]] | None
    key_prefixes: list[str]
    names_budget: int
    topics_budget: int
    min_chunks_name: int
    min_chunks_topic: int
    max_chunks_name: int
    max_chunks_topic: int
    min_docs: int
    entity_boost_hyphen: float
    entity_boost_single: float
    extra_blocklist: frozenset[str]
    blocklist_override: frozenset[str] | None
    event_bus: object | None  # EventBus; untyped here to avoid an import cycle


def _non_scope_key(request: HintRebuildRequest) -> tuple[object, ...]:
    """Fields that must already match before two requests can share a scan."""
    return (
        tuple(request.key_prefixes),
        request.names_budget,
        request.topics_budget,
        request.min_chunks_name,
        request.min_chunks_topic,
        request.max_chunks_name,
        request.max_chunks_topic,
        request.min_docs,
        request.entity_boost_hyphen,
        request.entity_boost_single,
        request.extra_blocklist,
        request.blocklist_override,
        request.event_bus,
    )


def _can_merge(earlier: HintRebuildRequest, later: HintRebuildRequest) -> bool:
    """True when a union would not apply one caller's scoring or scan mode to the other."""
    if (earlier.configured_scopes is None) != (later.configured_scopes is None):
        return False
    return _non_scope_key(earlier) == _non_scope_key(later)


def widen(earlier: HintRebuildRequest, later: HintRebuildRequest) -> HintRebuildRequest:
    """Union the scopes both requests would rewrite.

    A follow-up must not be narrower than either caller, or the call that was
    dropped would leave its scope on the older snapshot. Scoring fields stay as
    they are; callers that differ there are not merged.
    """
    if not _can_merge(earlier, later):
        raise ValueError("hint rebuild requests differ outside the scope set")

    covered_earlier = _covered_scopes(earlier)
    covered_later = _covered_scopes(later)
    if covered_earlier is None and covered_later is None:
        return replace(later, scope=None, configured_scopes=None)

    merged: dict[str, list[str]] = {}
    if earlier.configured_scopes:
        merged.update(earlier.configured_scopes)
    if later.configured_scopes:
        merged.update(later.configured_scopes)
    if covered_earlier is None or covered_later is None:
        return replace(later, scope=None, configured_scopes=merged or None)

    subset = {
        name: merged[name]
        for name in (covered_earlier | covered_later)
        if name in merged
    }
    if len(subset) == 1:
        return replace(later, scope=next(iter(subset)), configured_scopes=subset)
    return replace(later, scope=None, configured_scopes=subset)


def _covered_scopes(request: HintRebuildRequest) -> set[str] | None:
    """Scope names this request rewrites. None means the whole scope column."""
    if request.configured_scopes is None:
        return None
    if request.scope is None:
        return set(request.configured_scopes)
    return {request.scope}


class HintRebuildGate:
    """Serialize rebuilds for one database path."""

    def __init__(self) -> None:
        self._cond: asyncio.Condition | None = None
        self._running = False
        self._dirty = False
        self._pending: HintRebuildRequest | None = None
        self._deferred: list[HintRebuildRequest] = []
        self._leader: HintRebuildRequest | None = None
        self._last: dict[str, str] = {}
        self._error: BaseException | None = None
        self._token = 0
        self._done_token = 0

    def _condition(self) -> asyncio.Condition:
        # Created on the running loop. Import-time construction binds the wrong loop.
        if self._cond is None:
            self._cond = asyncio.Condition()
        return self._cond

    def _surface_deferred(self) -> None:
        if self._pending is None and self._deferred:
            self._pending = self._deferred.pop(0)

    async def run(
        self,
        request: HintRebuildRequest,
        worker: Callable[[HintRebuildRequest], Awaitable[dict[str, str]]],
    ) -> dict[str, str]:
        """Run ``worker`` or wait until the in-flight runner's follow-up finishes."""
        cond = self._condition()
        current, finished = await self._claim(cond, request)
        if finished is not None:
            return finished
        assert current is not None
        try:
            while True:
                result = await worker(current)
                async with cond:
                    if not self._dirty:
                        self._last = result
                        self._done_token = self._token
                        self._running = False
                        self._pending = None
                        self._leader = None
                        self._error = None
                        self._surface_deferred()
                        cond.notify_all()
                        return result
                    current = self._pending if self._pending is not None else current
                    self._pending = None
                    self._dirty = False
                    self._leader = current
        except asyncio.CancelledError:
            await self._release_cancelled(cond)
            raise
        except BaseException as exc:
            async with cond:
                self._error = exc
                self._running = False
                self._dirty = False
                self._pending = None
                self._deferred.clear()
                self._leader = None
                cond.notify_all()
            raise

    async def _claim(
        self,
        cond: asyncio.Condition,
        request: HintRebuildRequest,
    ) -> tuple[HintRebuildRequest | None, dict[str, str] | None]:
        """Wait out an incompatible or in-flight run, then either join or lead.

        The returned pair is ``(request, None)`` when this caller must run the
        worker, or ``(None, result)`` when a run that already included this
        caller has published.
        """
        await cond.acquire()
        try:
            included = False
            joined_token = -1
            yielded = False
            while True:
                if self._running:
                    leader = (
                        self._pending if self._pending is not None else self._leader
                    )
                    if leader is not None and _can_merge(leader, request):
                        self._pending = widen(leader, request)
                        self._dirty = True
                        included = True
                        joined_token = self._token
                    else:
                        included = False
                        joined_token = -1
                    while self._running:
                        await cond.wait()
                    yielded = False
                    if self._error is not None:
                        raise self._error
                    if (
                        included
                        and self._done_token == joined_token
                        and not self._dirty
                    ):
                        return None, self._last
                    continue

                self._surface_deferred()
                if self._pending is not None and (
                    included or _can_merge(self._pending, request)
                ):
                    current = (
                        self._pending if included else widen(self._pending, request)
                    )
                    self._pending = None
                    self._dirty = False
                elif self._pending is not None:
                    if not yielded:
                        yielded = True
                        await self._yield_to_owner(cond)
                        continue
                    self._deferred.append(self._pending)
                    self._pending = None
                    self._dirty = False
                    current = request
                else:
                    current = request
                    self._dirty = False
                self._token += 1
                self._running = True
                self._error = None
                self._leader = current
                cond.notify_all()
                return current, None
        finally:
            cond.release()

    async def _yield_to_owner(self, cond: asyncio.Condition) -> None:
        """Let a co-woken owner claim ``_pending`` before this caller stashes it.

        ``Condition`` is not reentrant. Dropping the lock and sleeping one turn
        gives that owner the acquire it is already queued on. Cancellation must
        still put the lock back, or the next rebuild waits forever.
        """
        task = asyncio.current_task()
        parked = task.cancelling() if task is not None else 0
        if task is not None:
            for _ in range(parked):
                task.uncancel()
        cond.release()
        try:
            await asyncio.sleep(0)
        finally:
            while True:
                try:
                    await cond.acquire()
                    break
                except asyncio.CancelledError:
                    if task is not None:
                        task.uncancel()
            if task is not None:
                for _ in range(parked):
                    task.cancel()

    async def _release_cancelled(self, cond: asyncio.Condition) -> None:
        """Leave pending work runnable. Do not store ``CancelledError`` for waiters.

        The cancelled task is the debounce (or whichever caller was the runner).
        Waiters blocked in ``commit`` only catch ``Exception``, so handing them
        this error drops the follow-up. Clearing ``_running`` and notifying lets
        one of them take ``_pending``.
        """
        task = asyncio.current_task()
        parked = task.cancelling() if task is not None else 0
        if task is not None:
            for _ in range(parked):
                task.uncancel()
        try:
            async with cond:
                self._running = False
                self._error = None
                self._surface_deferred()
                cond.notify_all()
        finally:
            if task is not None:
                for _ in range(parked):
                    task.cancel()


def gate_for(db_path: Path) -> HintRebuildGate:
    """Return the process-wide gate for this metadata database."""
    key = str(db_path)
    gate = _gates.get(key)
    if gate is None:
        gate = HintRebuildGate()
        _gates[key] = gate
    return gate


def reset_hint_rebuild_gates() -> None:
    """Drop gate state. Tests need a fresh condition on each event loop."""
    _gates.clear()
