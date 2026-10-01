"""In-flight registry for ``rag_search``: one backend search per identical request.

Who calls: ``tools/rag.py::rag_search`` (sole caller) — ``admit_search`` before
the Stargate ``rag-context`` call, then ``SearchTicket.wait``; ``attach_search``
serves ``rag(op="search", arguments={"search_id": ...})`` polls.

Why: ``rag_search`` blocks on Stargate for 5–240 s (cold reranker load
included). MCP clients enforce their own idle deadline (Cursor: 120 s), and a
caller that hits it re-issues the same query, multiplying backend load without
ever seeing a result. This module makes the wait bounded and the re-issue
harmless:

- identical requests (same canonical key from ``search_key``) share one backend
  search; later callers *attach* to the owner's result instead of starting a
  second search (``attached: true`` in their envelope);
- ``wait`` returns inside ``MCP_RAG_SEARCH_WAIT_S`` (default 90 s, under the
  120 s client wall) or raises ``SearchInFlightError`` carrying a ``search_id`` to
  poll with — the backend search keeps running on its worker thread;
- a finished search stays cached for ``MCP_RAG_SEARCH_RESULT_TTL_S`` (default
  600 s), so a poll or a redundant re-issue returns ``cache_hit: true`` without
  touching Stargate. A *failed* search is kept only ``FAILED_RESULT_TTL_S`` so
  pollers learn the failure, then the key is free for a fresh attempt.

Thread model: ``rag_search`` itself runs on the server's default thread pool
(``server.py`` runs sync tool functions off the event loop); the backend call
runs on a daemon worker thread capped by ``MCP_RAG_SEARCH_WORKERS`` (default 8)
so waiting callers never occupy a search slot and process exit is never held by
an in-flight search. All registry mutation happens under one lock.

Events (``mcp_events.record``, observation role): ``mcp.rag.search.attached``,
``mcp.rag.search.wait.exceeded``, ``mcp.rag.search.cache.hit``.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any

from mcp_events import record

WAIT_BUDGET_S = float(os.environ.get("MCP_RAG_SEARCH_WAIT_S", "90"))
RESULT_TTL_S = float(os.environ.get("MCP_RAG_SEARCH_RESULT_TTL_S", "600"))
FAILED_RESULT_TTL_S = 30.0
MAX_WORKERS = max(1, int(os.environ.get("MCP_RAG_SEARCH_WORKERS", "8")))

POLL_HINT = (
    "Search still running — this is not a failure. Poll with "
    'rag(op="search", arguments=\'{"search_id": "<search_id>"}\') or re-issue the '
    "identical call; both attach to the running search, neither starts a new one."
)


@dataclass
class _Entry:
    """Registry row: one backend search, shared by every caller with the same key."""

    search_id: str
    key: str
    future: Future
    admitted_at: float
    waiters: int = 1
    finished_at: float | None = None

    def failed(self) -> bool:
        return self.future.done() and self.future.exception() is not None


_lock = threading.Lock()
_by_key: dict[str, _Entry] = {}
_by_id: dict[str, _Entry] = {}
_slots = threading.Semaphore(MAX_WORKERS)


class SearchInFlightError(Exception):
    """Raised by ``SearchTicket.wait`` when the wait budget elapses before the result.

    Not a search failure: the backend search continues. ``envelope()`` renders
    the ``status: in_flight`` payload the MCP caller receives, including the
    ``search_id`` handle and the poll recipe.
    """

    def __init__(self, search_id: str, elapsed_s: float, wait_budget_s: float):
        super().__init__(f"search {search_id} still running after {elapsed_s:.1f}s")
        self.search_id = search_id
        self.elapsed_s = elapsed_s
        self.wait_budget_s = wait_budget_s

    def envelope(self) -> dict[str, Any]:
        """Render the caller-facing ``in_flight`` envelope (never an ``error`` key)."""
        return {
            "status": "in_flight",
            "pipeline": "rag-context",
            "search_id": self.search_id,
            "elapsed_s": round(self.elapsed_s, 1),
            "wait_budget_s": self.wait_budget_s,
            "poll": POLL_HINT,
        }


class UnknownSearchError(Exception):
    """Raised by ``attach_search`` when a ``search_id`` is unknown or has expired."""


@dataclass
class SearchTicket:
    """A caller's handle on one admitted search; returned by ``admit_search``/``attach_search``.

    ``attached`` is True when another caller owns the backend search; ``cache_hit``
    when the result had already finished at admission. ``wait`` blocks for the
    result within the budget; ``stamp`` adds the handle fields to any envelope the
    caller returns, so every reply — success, error, or ``in_flight`` — carries
    the same ``search_id``.
    """

    search_id: str
    attached: bool
    cache_hit: bool
    _entry: _Entry

    def wait(self, wait_s: float | None = None) -> Any:
        """Return the backend result, re-raising its exception for every waiter.

        Raises ``SearchInFlightError`` when ``wait_s`` (default ``WAIT_BUDGET_S``)
        elapses first; records ``mcp.rag.search.wait.exceeded``.
        """
        budget = WAIT_BUDGET_S if wait_s is None else wait_s
        try:
            return self._entry.future.result(timeout=budget)
        except FutureTimeout:
            elapsed = time.monotonic() - self._entry.admitted_at
            record(
                "mcp.rag.search.wait.exceeded",
                search_id=self.search_id,
                elapsed_s=round(elapsed, 1),
                wait_budget_s=budget,
                waiters=self._entry.waiters,
            )
            raise SearchInFlightError(self.search_id, elapsed, budget) from None

    @property
    def duration_s(self) -> float:
        """Wall time of the backend search itself (admission to finish, or to now)."""
        end = self._entry.finished_at or time.monotonic()
        return end - self._entry.admitted_at

    def stamp(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Return *envelope* with ``search_id`` (+ ``attached`` / ``cache_hit`` when true)."""
        fields: dict[str, Any] = {"search_id": self.search_id}
        if self.attached:
            fields["attached"] = True
        if self.cache_hit:
            fields["cache_hit"] = True
        return {**envelope, **fields}


def search_key(query: str, pipeline_options: dict[str, Any]) -> str:
    """Canonical identity of a search: whitespace-folded, case-folded query plus
    every pipeline option that changes retrieval (scope, prefixes, chunk cap)."""
    payload = {"q": " ".join(query.split()).lower(), "o": pipeline_options}
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def admit_search(key: str, start: Callable[[], Any]) -> SearchTicket:
    """Admit a search under *key*: start it, or attach to the identical one in flight.

    ``start`` runs exactly once per live key, on a worker thread, and may raise;
    its exception reaches every waiter through ``SearchTicket.wait``. A cached
    successful result is served without calling ``start`` (``cache_hit``). A
    cached failure is evicted and ``start`` runs again.
    """
    now = time.monotonic()
    with _lock:
        _prune_locked(now)
        entry = _by_key.get(key)
        if entry is not None and entry.failed():
            _evict_locked(entry)
            entry = None
        if entry is not None:
            entry.waiters += 1
            ticket = SearchTicket(
                search_id=entry.search_id,
                attached=True,
                cache_hit=entry.future.done(),
                _entry=entry,
            )
        else:
            entry = _Entry(
                search_id=f"rs-{uuid.uuid4().hex[:12]}",
                key=key,
                future=Future(),
                admitted_at=now,
            )
            _by_key[key] = entry
            _by_id[entry.search_id] = entry
            ticket = SearchTicket(
                search_id=entry.search_id, attached=False, cache_hit=False, _entry=entry
            )

    if ticket.cache_hit:
        record(
            "mcp.rag.search.cache.hit",
            search_id=entry.search_id,
            age_s=round(now - (entry.finished_at or now), 1),
        )
    elif ticket.attached:
        record(
            "mcp.rag.search.attached",
            search_id=entry.search_id,
            waiters=entry.waiters,
            elapsed_s=round(now - entry.admitted_at, 1),
        )
    else:
        threading.Thread(
            target=_run,
            args=(entry, start),
            name=f"rag-search-{entry.search_id}",
            daemon=True,
        ).start()
    return ticket


def attach_search(search_id: str) -> SearchTicket:
    """Attach to a previously admitted search by handle (the poll path).

    Raises ``UnknownSearchError`` when the id was never issued or its cached result
    has expired; the caller turns that into an ``error`` envelope.
    """
    now = time.monotonic()
    with _lock:
        _prune_locked(now)
        entry = _by_id.get(search_id)
        if entry is None:
            raise UnknownSearchError(search_id)
        entry.waiters += 1
        cache_hit = entry.future.done() and not entry.failed()
    record(
        "mcp.rag.search.cache.hit" if cache_hit else "mcp.rag.search.attached",
        search_id=search_id,
        waiters=entry.waiters,
        elapsed_s=round(now - entry.admitted_at, 1),
    )
    return SearchTicket(
        search_id=search_id, attached=True, cache_hit=cache_hit, _entry=entry
    )


def _run(entry: _Entry, start: Callable[[], Any]) -> None:
    """Worker body: take a slot, run the backend search, publish result or exception."""
    with _slots:
        try:
            result = start()
        except BaseException as exc:  # noqa: BLE001 — every waiter must see the failure
            entry.finished_at = time.monotonic()
            entry.future.set_exception(exc)
            return
        entry.finished_at = time.monotonic()
        entry.future.set_result(result)


def _prune_locked(now: float) -> None:
    expired = [
        e
        for e in _by_id.values()
        if e.finished_at is not None
        and now - e.finished_at > (FAILED_RESULT_TTL_S if e.failed() else RESULT_TTL_S)
    ]
    for entry in expired:
        _evict_locked(entry)


def _evict_locked(entry: _Entry) -> None:
    _by_id.pop(entry.search_id, None)
    if _by_key.get(entry.key) is entry:
        _by_key.pop(entry.key, None)
