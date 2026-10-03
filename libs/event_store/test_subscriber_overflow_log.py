"""Fan-out overflow logging stays off the per-subscriber hot path."""

from __future__ import annotations

import asyncio
import logging

import pytest

from event_store.ingest import IngestServer


def _full_queue() -> asyncio.Queue[dict[str, object]]:
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=1)
    queue.put_nowait({"signal": "prefill"})
    return queue


def test_subscriber_overflow_warning_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Hundreds of full queues must not emit one warning per drop.

    Fan-out runs on the event-loop thread. A log line per full subscriber
    per event dirties the log fast enough to stall that thread in writeback,
    and the observability query then misses its client timeout.
    """
    clock = {"now": 1000.0}
    monkeypatch.setattr("event_store.ingest.time.monotonic", lambda: clock["now"])
    queues = {_full_queue() for _ in range(10)}
    server = IngestServer(
        None,  # type: ignore[arg-type]
        "/tmp/unused-events.sock",
        queues,
        drop_notice_interval_sec=1.0,
    )

    with caplog.at_level(logging.WARNING, logger="event_store.ingest"):
        for _ in range(5):
            server._fan_out({"signal": "live"})
        clock["now"] = 1001.1
        server._fan_out({"signal": "live"})

    warnings = [
        record for record in caplog.records if record.levelno >= logging.WARNING
    ]
    assert len(warnings) == 2
    assert "dropped 10 event deliveries across 10 subscribers" in warnings[0].message
    assert "dropped 50 event deliveries across 10 subscribers" in warnings[1].message
    for queue in queues:
        item = queue.get_nowait()
        assert item["signal"] == "events.dropped.subscribe"
