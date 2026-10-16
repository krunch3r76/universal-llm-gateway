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
async def test_continuous_stall_dumps_again_at_long_threshold(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One long sync stall emits a threshold dump and a second at RAG_LOOP_STACK_LONG_S."""
    _arm(
        monkeypatch,
        RAG_LOOP_STACK_LATE_S="0.4",
        RAG_LOOP_STACK_LONG_S="0.8",
        RAG_LOOP_STACK_MIN_INTERVAL_S="30",
    )
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    start_loop_instrumentation()
    try:
        time.sleep(1.05)
        await asyncio.sleep(0.15)
        dumps = _dumps(caplog)
        assert len(dumps) >= 2, "expected threshold and long-stall stack dumps"
        late_vals = []
        for msg in dumps:
            match = re.search(r"late_ms=([\d.]+)", msg)
            assert match is not None
            late_vals.append(float(match.group(1)))
        assert late_vals[0] < 600
        assert late_vals[1] >= 750
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


@pytest.mark.asyncio
async def test_long_second_stall_dumps_after_interval(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second stall that begins inside min_interval and runs past it dumps."""
    _arm(
        monkeypatch,
        RAG_LOOP_STACK_LATE_S="0.05",
        RAG_LOOP_STACK_LONG_S="0.12",
        RAG_LOOP_STACK_MIN_INTERVAL_S="0.30",
        RAG_LOOP_STACK_POLL_S="0.02",
        RAG_LOOP_STACK_SAMPLE_S="5",
    )
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    start_loop_instrumentation()
    try:
        time.sleep(0.18)
        await asyncio.sleep(0.05)
        after_first = len(_dumps(caplog))
        assert after_first >= 1
        time.sleep(0.90)
        await asyncio.sleep(0.05)
        assert len(_dumps(caplog)) > after_first
    finally:
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_unset_dump_cap_uses_default(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset RAG_LOOP_STACK_DUMP_CAP keeps the default and still dumps."""
    monkeypatch.delenv("RAG_LOOP_STACK_DUMP_CAP", raising=False)
    _arm(
        monkeypatch,
        RAG_LOOP_STACK_LATE_S="0.05",
        RAG_LOOP_STACK_POLL_S="0.02",
        RAG_LOOP_STACK_SAMPLE_S="5",
        RAG_LOOP_STACK_LONG_S="10",
    )
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    start_loop_instrumentation()
    try:
        time.sleep(0.30)
        await asyncio.sleep(0.05)
        assert len(_dumps(caplog)) == 1
    finally:
        await stop_loop_instrumentation()


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", ["0", "-3"])
async def test_nonpositive_dump_cap_emits_nothing(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    cap: str,
) -> None:
    """Zero or negative RAG_LOOP_STACK_DUMP_CAP emits no stack dump."""
    _arm(
        monkeypatch,
        RAG_LOOP_STACK_LATE_S="0.05",
        RAG_LOOP_STACK_POLL_S="0.02",
        RAG_LOOP_STACK_DUMP_CAP=cap,
        RAG_LOOP_STACK_SAMPLE_S="0.05",
        RAG_LOOP_STACK_LONG_S="0.08",
    )
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    start_loop_instrumentation()
    try:
        time.sleep(0.30)
        await asyncio.sleep(0.05)
        assert _dumps(caplog) == []
    finally:
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_continuous_stall_samples_until_cap(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One stall longer than the sample interval dumps again until the cap."""
    _arm(
        monkeypatch,
        RAG_LOOP_STACK_LATE_S="0.15",
        RAG_LOOP_STACK_LONG_S="100",
        RAG_LOOP_STACK_SAMPLE_S="0.12",
        RAG_LOOP_STACK_DUMP_CAP="4",
        RAG_LOOP_STACK_MIN_INTERVAL_S="30",
        RAG_LOOP_STACK_POLL_S="0.02",
    )
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    start_loop_instrumentation()
    try:
        time.sleep(1.0)
        await asyncio.sleep(0.05)
        dumps = _dumps(caplog)
        assert len(dumps) == 4, dumps
    finally:
        await stop_loop_instrumentation()
