"""Unit tests for watcher verdict derivation and emission."""

from __future__ import annotations

import io

from bus_watch.verdict import (
    derive_verdict,
    emit_verdict_changed,
    should_emit_verdict_changed,
)


def test_derive_verdict_in_flight_while_grace_active() -> None:
    producer = {"state": "in_flight", "execution_id": "abc"}
    assert (
        derive_verdict(
            producer=producer,
            producer_grace_expired=False,
            stall_active=True,
        )
        == "in_flight"
    )


def test_derive_verdict_stalled_when_predicate_fires() -> None:
    producer = {"state": "in_flight", "execution_id": "abc"}
    assert (
        derive_verdict(
            producer=producer,
            producer_grace_expired=True,
            stall_active=True,
        )
        == "stalled"
    )


def test_derive_verdict_terminal_no_reply_without_stall() -> None:
    producer = {"state": "terminal", "terminal_status": "failed"}
    assert (
        derive_verdict(
            producer=producer,
            producer_grace_expired=False,
            stall_active=False,
        )
        == "terminal_no_reply"
    )


def test_derive_verdict_unlinked_and_unknown() -> None:
    assert (
        derive_verdict(
            producer={"state": "unlinked"},
            producer_grace_expired=False,
            stall_active=False,
        )
        == "unlinked"
    )
    assert (
        derive_verdict(
            producer={"state": "unknown"},
            producer_grace_expired=False,
            stall_active=False,
        )
        == "unknown_producer"
    )


def test_derive_verdict_default_polling() -> None:
    assert (
        derive_verdict(
            producer={"state": "in_flight"},
            producer_grace_expired=True,
            stall_active=False,
        )
        == "polling"
    )


def test_should_emit_verdict_changed_dedupes() -> None:
    emit, last = should_emit_verdict_changed(last_verdict=None, verdict="in_flight")
    assert emit
    assert last == "in_flight"
    emit, last = should_emit_verdict_changed(last_verdict=last, verdict="in_flight")
    assert not emit
    assert last == "in_flight"


def test_emit_verdict_changed_format() -> None:
    buf = io.StringIO()
    emit_verdict_changed(
        label="o2-fable",
        thread="10303",
        execution_id="605698ed-880e-4847-81d4-5e0978f0e44b",
        from_verdict="",
        to_verdict="in_flight",
        stream=buf,
    )
    assert (
        buf.getvalue()
        == "verdict-changed: label=o2-fable thread=10303 "
        "execution_id=605698ed-880e-4847-81d4-5e0978f0e44b from= to=in_flight\n"
    )
