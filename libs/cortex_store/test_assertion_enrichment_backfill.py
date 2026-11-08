"""Unit tests for assertion enrichment backfill selection helpers."""

from __future__ import annotations

import sqlite3

from cortex_store.assertion_enrichment_backfill import (
    SkipClass,
    count_backfill_candidates,
    enrichment_skip_reason,
    events_array_len,
    pipeline_payload,
    prospective_question_form_share,
)


def test_enrichment_skip_classes() -> None:
    assert enrichment_skip_reason("Assistant: foo") == SkipClass.ASSISTANT_PREFIX
    assert enrichment_skip_reason("x [UNGROUNDED] y") == SkipClass.UNGROUNDED
    assert enrichment_skip_reason("") == SkipClass.EMPTY_CLAIM
    assert enrichment_skip_reason("valid claim") is None


def test_prospective_question_share() -> None:
    share = prospective_question_form_share(
        ["What happens next?", "Future migrations touch this service."]
    )
    assert share == 0.5


def test_pipeline_payload_redo_flag() -> None:
    payload = pipeline_payload(
        {"id": 9, "claim": "c", "entity_id": "e:1", "confidence": "believed"},
        kinds=["events"],
        redo=True,
    )
    assert payload["pipeline_options"]["redo"] is True


def test_events_array_len() -> None:
    assert events_array_len('[{"event":"a"}]') == 1
    assert events_array_len("not json") == -1


def test_count_candidates_gaps_only(tmp_path: object) -> None:
    from pathlib import Path

    db = Path(tmp_path) / "cortex.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE assertions ("
        "id INTEGER PRIMARY KEY, claim TEXT, superseded_by INTEGER, "
        "events_json TEXT, prospective_summary TEXT)"
    )
    conn.execute(
        "INSERT INTO assertions (id, claim, events_json, prospective_summary) "
        "VALUES (1, 'good', NULL, 'x'), (2, 'Assistant: bad', NULL, NULL)"
    )
    conn.commit()
    report = count_backfill_candidates(conn, kinds=("events",), redo=False)
    assert report.active_total == 2
    assert report.skip_by_class.get("assistant_prefix") == 1
    assert report.eligible == 1
    conn.close()
