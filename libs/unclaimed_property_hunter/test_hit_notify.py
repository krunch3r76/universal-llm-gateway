"""Tests for hunter hit notify amount floor and digest path."""

from __future__ import annotations

from unclaimed_property_hunter.diff_runs import RunDiff
from unclaimed_property_hunter.hit_notify import (
    PAGE_AMOUNT_FLOOR,
    classify_new_hit,
    decide_notifications,
    format_digest_note,
    parse_amount_usd,
)
from unclaimed_property_hunter.models import Hit, Query, RunRecord


def _record(*hits: Hit) -> RunRecord:
    return RunRecord(
        run_id="test-run",
        utc_timestamp="2026-08-14T12:00:00Z",
        query=Query(surname="Testsubject"),
        run_kind="bulk_extract",
        search_executed=True,
        raw_payload_uri="cortex://notes/system/unclaimed-property/runs/t.raw",
        raw_sha256="abc",
        hits=list(hits),
    )


def test_parse_amount_usd():
    assert parse_amount_usd("$25.00") == 25.0
    assert parse_amount_usd("0.17") == 0.17


def test_classify_new_hit_floor():
    above = Hit(property_id="1", amount_or_range="25.00")
    below = Hit(property_id="2", amount_or_range="0.17")
    pru = Hit(property_id="3", holder="Prudential Life", amount_or_range="0.01")
    assert classify_new_hit(above) == "page"
    assert classify_new_hit(below) == "digest"
    assert classify_new_hit(pru) == "page"


def test_decide_notifications_empty_prudential_no_page():
    record = _record()
    decision = decide_notifications(record, RunDiff(added=[], disappeared=[], changed=[]))
    assert decision.page_hits == ()
    assert decision.reason == "no_new_hits"


def test_decide_notifications_sub_floor_digest_only():
    hit = Hit(property_id="1036109882", amount_or_range="0.17")
    record = _record(hit)
    decision = decide_notifications(
        record,
        RunDiff(added=["1036109882"], disappeared=[], changed=[]),
    )
    assert decision.page_hits == ()
    assert len(decision.digest_hits) == 1
    note = format_digest_note(decision.digest_hits)
    assert "1036109882" in note
    assert PAGE_AMOUNT_FLOOR == 6.0
