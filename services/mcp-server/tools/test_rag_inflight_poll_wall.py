"""In-flight registry: search_id poll shape and max wall timeout."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

_MCP = Path(__file__).resolve().parents[1]
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

from tools import _rag_inflight  # noqa: E402
from tools._rag_inflight import (  # noqa: E402
    SearchInFlightError,
    admit_search,
    attach_search,
)
from tools.rag import register_rag_tools  # noqa: E402


def _clear_registry() -> None:
    with _rag_inflight._lock:
        _rag_inflight._by_key.clear()
        _rag_inflight._by_id.clear()


def _rag_search_tool() -> Any:
    captured: list[Any] = []

    class _FakeMcp:
        def tool(self, **_kwargs: Any) -> Any:
            def _decorator(fn: Any) -> Any:
                captured.append(fn)
                return fn

            return _decorator

    register_rag_tools(_FakeMcp())
    for fn in captured:
        if fn.__name__ == "rag_search":
            return fn
    raise AssertionError("rag_search not registered")


def test_rag_search_accepts_search_id_without_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poll path from a:40568 — search_id-only must not require query."""
    _clear_registry()
    monkeypatch.setattr(_rag_inflight, "WAIT_BUDGET_S", 0.05)
    started = threading.Event()
    release = threading.Event()

    def start() -> dict[str, Any]:
        started.set()
        assert release.wait(2)
        return {"status": "ok", "context": "body", "pipeline": "rag-search"}

    try:
        first = admit_search("poll-key", start)
        assert started.wait(2)
        with pytest.raises(SearchInFlightError):
            first.wait()

        rag_search = _rag_search_tool()
        polled = rag_search(search_id=first.search_id)
        assert polled["status"] == "in_flight"
        assert polled["search_id"] == first.search_id

        release.set()
        done = rag_search(search_id=first.search_id)
        assert done["status"] == "ok"
        assert done["search_id"] == first.search_id
        assert done.get("attached") is True
    finally:
        _clear_registry()


def test_identical_in_flight_reissue_shares_search_id() -> None:
    _clear_registry()
    started = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def start() -> dict[str, Any]:
        calls["n"] += 1
        started.set()
        assert release.wait(2)
        return {"status": "ok", "context": "shared"}

    try:
        first = admit_search("dedupe-key", start)
        assert started.wait(2)
        with pytest.raises(SearchInFlightError):
            first.wait(0.05)
        second = admit_search("dedupe-key", start)
        assert second.search_id == first.search_id
        assert second.attached is True
        assert calls["n"] == 1
        release.set()
        assert second.wait()["context"] == "shared"
    finally:
        _clear_registry()


def test_max_wall_returns_timeout_not_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stuck backend search must surface terminal timeout after MAX_WALL_S."""
    _clear_registry()
    monkeypatch.setattr(_rag_inflight, "MAX_WALL_S", 0.12)
    monkeypatch.setattr(_rag_inflight, "WAIT_BUDGET_S", 0.04)

    def start() -> dict[str, Any]:
        time.sleep(1.0)
        return {"status": "ok", "context": "late"}

    try:
        ticket = admit_search("wall-key", start)
        with pytest.raises(SearchInFlightError):
            ticket.wait()
        time.sleep(0.1)
        timed = attach_search(ticket.search_id)
        env = timed.stamp(timed.wait())
        assert env["status"] == "timeout"
        assert env["retryable"] is True
        assert env["search_id"] == ticket.search_id
    finally:
        _clear_registry()


def test_timeout_allows_fresh_search_same_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_registry()
    monkeypatch.setattr(_rag_inflight, "MAX_WALL_S", 0.1)
    monkeypatch.setattr(_rag_inflight, "WAIT_BUDGET_S", 0.03)
    starts = {"n": 0}

    def start() -> dict[str, Any]:
        starts["n"] += 1
        time.sleep(0.5)
        return {"status": "ok", "context": "x"}

    try:
        first = admit_search("retry-key", start)
        with pytest.raises(SearchInFlightError):
            first.wait()
        time.sleep(0.08)
        env = first.stamp(first.wait())
        assert env["status"] == "timeout"
        second = admit_search("retry-key", start)
        assert second.cache_hit is False
        assert second.search_id != first.search_id
    finally:
        _clear_registry()
