"""Unit tests for densify review query operations."""

from __future__ import annotations

import time

import pytest

from event_store.operation_catalog import get_operation
from event_store.operation_dispatch import _DISPATCH
from event_store.operations_densify_review import (
    _densify_review_admitted,
    _densify_review_outcome,
)
from event_store.store import EventStore


@pytest.mark.asyncio
async def test_tripwire_operations_registered() -> None:
    assert get_operation("frontier.densify.review.admitted") is not None
    assert get_operation("frontier.densify.review.outcome") is not None
    assert "frontier.densify.review.admitted" in _DISPATCH
    assert "frontier.densify.review.outcome" in _DISPATCH


@pytest.mark.asyncio
async def test_admitted_tripwire_opt_out_rate() -> None:
    now_ms = int(time.time() * 1000)
    store = EventStore(":memory:")
    await store.open()
    try:
        await store.insert_events(
            [
                {
                    "signal": "frontier.densify.review.admitted",
                    "role": "node",
                    "scope": "node",
                    "ts_unix_ms": now_ms,
                    "timestamp": "2026-06-13T00:00:00Z",
                    "source": "test",
                    "payload": {"opt_out": True},
                },
                {
                    "signal": "frontier.densify.review.admitted",
                    "role": "node",
                    "scope": "node",
                    "ts_unix_ms": now_ms + 1,
                    "timestamp": "2026-06-13T00:00:01Z",
                    "source": "test",
                    "payload": {"opt_out": False},
                },
            ]
        )
        result = await _densify_review_admitted({"minutes": 60}, store)
    finally:
        await store.close()

    assert result["count"] == 2
    assert result["opt_out_count"] == 1
    assert result["opt_out_rate"] == 0.5


@pytest.mark.asyncio
async def test_outcome_tripwire_finding_delta() -> None:
    now_ms = int(time.time() * 1000)
    store = EventStore(":memory:")
    await store.open()
    try:
        await store.insert_events(
            [
                {
                    "signal": "frontier.densify.review.outcome",
                    "role": "node",
                    "scope": "node",
                    "ts_unix_ms": now_ms,
                    "timestamp": "2026-06-13T00:00:00Z",
                    "source": "test",
                    "payload": {
                        "finding_delta": 2,
                        "reviewer_concur_only": False,
                    },
                }
            ]
        )
        result = await _densify_review_outcome({"minutes": 60}, store)
    finally:
        await store.close()

    assert result["count"] == 1
    assert result["avg_finding_delta"] == 2.0
