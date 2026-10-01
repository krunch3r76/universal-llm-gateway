"""One corpus-hint rebuild at a time, with waiters collapsed into one rerun.

Off-thread scans can overlap. A slower scan that started earlier can commit
after a newer one and put stale hints back. The gate holds a single runner.
Callers that arrive while it runs set a dirty flag and wait; their scope sets
are unioned so the runner does one follow-up instead of one scan each.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path

_gates: dict[str, HintRebuildGate] = {}


@dataclass
class HintRebuildRequest:
    """Arguments for one hint rebuild. Scoring fields come from the later caller."""

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


def widen(earlier: HintRebuildRequest, later: HintRebuildRequest) -> HintRebuildRequest:
    """Union the scopes both requests would rewrite. Keep the later scoring params.

    A follow-up must not be narrower than either caller, or the call that was
    dropped would leave its scope on the older snapshot.
    """
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
        name: merged[name] for name in (covered_earlier | covered_later) if name in merged
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
        self._leader: HintRebuildRequest | None = None
        self._last: dict[str, str] = {}
        self._error: BaseException | None = None

    def _condition(self) -> asyncio.Condition:
        # Created on the running loop. Import-time construction binds the wrong loop.
        if self._cond is None:
            self._cond = asyncio.Condition()
        return self._cond

    async def run(
        self,
        request: HintRebuildRequest,
        worker: Callable[[HintRebuildRequest], Awaitable[dict[str, str]]],
    ) -> dict[str, str]:
        """Run ``worker`` or wait until the in-flight runner's follow-up finishes."""
        cond = self._condition()
        async with cond:
            if self._running:
                base = self._pending if self._pending is not None else self._leader
                if base is None:
                    base = request
                self._pending = widen(base, request)
                self._dirty = True
                while self._running:
                    await cond.wait()
                if self._error is not None:
                    raise self._error
                return self._last
            self._running = True
            self._error = None
            self._leader = request
            current = request

        try:
            while True:
                result = await worker(current)
                async with cond:
                    if not self._dirty:
                        self._last = result
                        self._running = False
                        self._pending = None
                        cond.notify_all()
                        return result
                    self._dirty = False
                    current = self._pending if self._pending is not None else current
                    self._pending = None
                    self._leader = current
        except BaseException as exc:
            async with cond:
                self._error = exc
                self._running = False
                self._dirty = False
                self._pending = None
                cond.notify_all()
            raise


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
