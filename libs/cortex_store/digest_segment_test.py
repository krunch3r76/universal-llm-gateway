"""Hermetic tests for journal-digest SEGMENT helper."""

from __future__ import annotations

import pytest

from cortex_store.digest_segment import (
    Segment,
    aggregate_auto_segment_digest,
    heading_to_slug,
    segment_journal_entry,
)

_ENTRY_DATE = "2026-07-13"

_SNAPSHOT_2026_07_13 = """\
7/13/2026 Monday

# Health
Ingested 25 mg of AnazaoHealth enclomiphene 25 mg sublingually in the morning.
Ingested 200mg of caffeine tablets in the morning.
Ingested 200mg of caffeine tablets in the evening.

# Health symptoms
Nodding off repeatedly in the day, entering a dream state. Remedied by 200mg of caffeine.

# Wells Fargo calls for payment on my PLOC
A Michael (?) from Wells Fargo called to inform me my payment is 5 days overdue on my PLOC.

# Filed a dispute with PG&E about the backbilling of gas charges
Spoke with Marlena after making a call to PG&E at 7:43AM to 800-743-5000.

# Esco from Chase Executive Department returns my call.
Esco called me at 10:11AM

# Paid some outstanding fast-trak bills

# Carol Bowman calls for a favor
Carol gave me the number to her insurance agent:
"""

_EXPECTED_H1_ANCHORS = [
    f"{_ENTRY_DATE}#health",
    f"{_ENTRY_DATE}#health-symptoms",
    f"{_ENTRY_DATE}#wells-fargo-calls-for-payment-on-my-ploc",
    f"{_ENTRY_DATE}#filed-a-dispute-with-pg-e-about-the-backbilling-of-gas-charges",
    f"{_ENTRY_DATE}#esco-from-chase-executive-department-returns-my-call",
    f"{_ENTRY_DATE}#paid-some-outstanding-fast-trak-bills",
    f"{_ENTRY_DATE}#carol-bowman-calls-for-a-favor",
]


@pytest.mark.offline
def test_heading_to_slug_kebab() -> None:
    assert heading_to_slug("Health symptoms") == "health-symptoms"
    assert heading_to_slug("PG&E") == "pg-e"


@pytest.mark.offline
def test_segment_snapshot_yields_seven_h1_anchors() -> None:
    segments = segment_journal_entry(_SNAPSHOT_2026_07_13, entry_date=_ENTRY_DATE)
    h1_segments = [s for s in segments if not s.entry_anchor.endswith("#preamble")]
    assert len(h1_segments) == 7
    assert [s.entry_anchor for s in h1_segments] == _EXPECTED_H1_ANCHORS


@pytest.mark.offline
def test_segment_includes_h1_heading_line() -> None:
    segments = segment_journal_entry(_SNAPSHOT_2026_07_13, entry_date=_ENTRY_DATE)
    health = next(s for s in segments if s.entry_anchor.endswith("#health"))
    assert health.entry_text.startswith("# Health")
    assert "enclomiphene" in health.entry_text


@pytest.mark.offline
def test_preamble_emitted_when_non_whitespace_before_first_h1() -> None:
    segments = segment_journal_entry(_SNAPSHOT_2026_07_13, entry_date=_ENTRY_DATE)
    preamble = next(s for s in segments if s.entry_anchor.endswith("#preamble"))
    assert "7/13/2026 Monday" in preamble.entry_text


@pytest.mark.offline
def test_preamble_omitted_when_only_whitespace_before_first_h1() -> None:
    text = "\n\n# Only section\nBody text.\n"
    segments = segment_journal_entry(text, entry_date=_ENTRY_DATE)
    assert len(segments) == 1
    assert segments[0].entry_anchor == f"{_ENTRY_DATE}#only-section"


@pytest.mark.offline
def test_empty_text_yields_no_segments() -> None:
    assert segment_journal_entry("", entry_date=_ENTRY_DATE) == []
    assert segment_journal_entry("   \n\n  ", entry_date=_ENTRY_DATE) == []


@pytest.mark.offline
def test_duplicate_h1_headings_get_suffixed_anchors() -> None:
    """Sol F3 — identical H1s must not share one watermark anchor."""
    text = "# Repeat\nfirst body\n\n# Repeat\nsecond body\n"
    segments = segment_journal_entry(text, entry_date=_ENTRY_DATE)
    anchors = [s.entry_anchor for s in segments]
    assert anchors == [
        f"{_ENTRY_DATE}#repeat",
        f"{_ENTRY_DATE}#repeat-2",
    ]
    assert "first body" in segments[0].entry_text
    assert "second body" in segments[1].entry_text


@pytest.mark.offline
def test_aggregate_auto_segment_collects_per_section_results() -> None:
    calls: list[Segment] = []

    def fake_digest(**kwargs: object) -> dict[str, object]:
        calls.append(
            Segment(
                entry_anchor=str(kwargs["entry_anchor"]),
                heading="",
                entry_text=str(kwargs["entry_text"]),
            )
        )
        return {"status": "staged", "ledger_id": len(calls)}

    result = aggregate_auto_segment_digest(
        fake_digest,
        journal_entity_id="document:test",
        entry_text=_SNAPSHOT_2026_07_13,
        entry_date=_ENTRY_DATE,
    )

    h1_calls = [c for c in calls if not c.entry_anchor.endswith("#preamble")]
    assert len(h1_calls) == 7
    assert result["status"] == "segmented"
    assert result["summary"]["staged"] == len(calls)
    assert len(result["sections"]) == len(calls)
