"""Tests for ProducerGrace wall-clock expiry."""

from __future__ import annotations

from bus_watch.producer_grace import ProducerGrace

_GATE0_PRODUCER = {
    "execution_id": "d6a93d64-18a9-4779-8238-89d6af49e415",
    "pipeline_id": "cdp-generate",
    "state": "in_flight",
    "terminal_status": None,
    "linked_at": "2026-09-07T05:41:20Z",
    "delivery_at": None,
    "source": "thread_dispatch_links",
}


def test_producer_grace_not_expired_before_ceiling() -> None:
    clock = {"now": 1000.0}

    def now_fn() -> float:
        return clock["now"]

    grace = ProducerGrace(grace_seconds=900.0, now_fn=now_fn)
    assert grace.observe(_GATE0_PRODUCER, turn_count=60) is False
    clock["now"] = 1700.0
    assert grace.observe(_GATE0_PRODUCER, turn_count=60) is False
    clock["now"] = 1900.0
    assert grace.observe(_GATE0_PRODUCER, turn_count=60) is True


def test_producer_grace_resets_on_turn_count_advance() -> None:
    clock = {"now": 0.0}

    def now_fn() -> float:
        return clock["now"]

    grace = ProducerGrace(grace_seconds=60.0, now_fn=now_fn)
    assert grace.observe(_GATE0_PRODUCER, turn_count=60) is False
    clock["now"] = 120.0
    assert grace.observe(_GATE0_PRODUCER, turn_count=61) is False
    clock["now"] = 170.0
    assert grace.observe(_GATE0_PRODUCER, turn_count=61) is False
    clock["now"] = 181.0
    assert grace.observe(_GATE0_PRODUCER, turn_count=61) is True


def test_producer_grace_resets_on_producer_fingerprint_change() -> None:
    clock = {"now": 0.0}

    def now_fn() -> float:
        return clock["now"]

    grace = ProducerGrace(grace_seconds=60.0, now_fn=now_fn)
    assert grace.observe(_GATE0_PRODUCER, turn_count=60) is False
    clock["now"] = 120.0
    terminal = {**_GATE0_PRODUCER, "state": "terminal", "terminal_status": "failed"}
    assert grace.observe(terminal, turn_count=60) is False
