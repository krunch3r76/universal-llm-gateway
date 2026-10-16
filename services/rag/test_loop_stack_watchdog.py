"""Stack-watchdog tests for friction:39420 r3.

A blocked loop thread must emit one ``loop_stack_dump``. An idle loop, a
disabled flag, and a second stall inside the rate-limit window must not.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time

import pytest

from services.rag.rag_service.loop_instrumentation import (
    start_loop_instrumentation,
    stop_loop_instrumentation,
)

_LOGGER = "services.rag.rag_service.loop_instrumentation"


def _dumps(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.message
        for record in caplog.records
        if record.levelno >= logging.WARNING and "loop_stack_dump" in record.message
    ]


def _arm(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    monkeypatch.setenv("RAG_LOOP_STACK_LATE_S", "0.4")
    monkeypatch.setenv("RAG_LOOP_STACK_POLL_S", "0.05")
    monkeypatch.setenv("RAG_LOOP_STACK_MIN_INTERVAL_S", "30")
    monkeypatch.setenv("RAG_LOOP_STACK_FRAMES", "12")
    monkeypatch.setenv("RAG_LOOP_STACK_WATCHDOG", "1")
    for key, value in env.items():
        monkeypatch.setenv(key, value)


@pytest.mark.asyncio
async def test_blocked_loop_dumps_sleeping_function(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """time.sleep on the loop thread (not to_thread) names that function."""
    _arm(monkeypatch)
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    start_loop_instrumentation()
    try:

        def _watchdog_block_sleep() -> None:
            time.sleep(0.9)

        _watchdog_block_sleep()
        await asyncio.sleep(0.15)
        dumps = _dumps(caplog)
        assert dumps, "expected loop_stack_dump while the loop thread slept"
        assert "_watchdog_block_sleep" in dumps[0]
        match = re.search(r"thread=(\S+)", dumps[0])
        assert match is not None
        assert match.group(1) == threading.current_thread().name
        assert match.group(1) != "rag-loop-stack-watchdog"
    finally:
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_idle_await_does_not_dump(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """await asyncio.sleep keeps the drift heartbeat fresh, so no dump."""
    _arm(monkeypatch)
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    start_loop_instrumentation()
    try:
        await asyncio.sleep(0.9)
        assert _dumps(caplog) == []
    finally:
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_watchdog_flag_off_skips_dump(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RAG_LOOP_STACK_WATCHDOG=0 plus a blocking sleep emits no dump."""
    _arm(monkeypatch, RAG_LOOP_STACK_WATCHDOG="0")
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    start_loop_instrumentation()
    try:
        time.sleep(0.9)
        await asyncio.sleep(0.15)
        assert _dumps(caplog) == []
    finally:
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_rate_limit_one_dump_per_interval(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second stall before min_interval, after recovery, stays silent."""
    _arm(monkeypatch, RAG_LOOP_STACK_MIN_INTERVAL_S="30")
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    start_loop_instrumentation()
    try:
        time.sleep(0.9)
        await asyncio.sleep(0.2)
        time.sleep(0.9)
        await asyncio.sleep(0.15)
        assert len(_dumps(caplog)) == 1
    finally:
        await stop_loop_instrumentation()
