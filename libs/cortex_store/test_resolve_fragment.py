"""Unit tests for the cortex:// URI fragment extension (spec § 2.2).

Covers:
  * ``parse_cortex_uri`` extracts the fragment as ``pinpoint``.
  * No fragment → ``pinpoint`` is None (back-compat with pre-spec URIs).
  * Fragment + revision query both parse independently.
  * ``_resolve_pinpoint_chunk`` returns the matching chunk row, or None
    on miss (caller surfaces this as ``pinpoint_unresolved``).
"""

from __future__ import annotations

import sqlite3

from cortex_store.routes.resolve import (
    _resolve_pinpoint_chunk,
    parse_cortex_uri,
)


def test_parse_uri_extracts_pinpoint() -> None:
    parsed = parse_cortex_uri("cortex://legal_source/rtc-63.2#f-1-B")
    assert parsed["entity_id"] == "legal_source:rtc-63.2"
    assert parsed["pinpoint"] == "f-1-B"
    assert parsed["revision"] is None


def test_parse_uri_no_fragment_yields_none_pinpoint() -> None:
    parsed = parse_cortex_uri("cortex://legal_source/rtc-63.2")
    assert parsed["pinpoint"] is None


def test_parse_uri_fragment_and_revision() -> None:
    parsed = parse_cortex_uri("cortex://case-law/larson-v-duca-1989?r=2#327")
    assert parsed["pinpoint"] == "327"
    assert parsed["revision"] == 2


def test_parse_uri_assertion_special_form_no_fragment() -> None:
    parsed = parse_cortex_uri("cortex://assertion/9243")
    assert parsed["entity_id"] == "assertion:9243"
    assert parsed["pinpoint"] is None


def _chunks_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE chunks ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  content TEXT,"
        "  source_uri TEXT,"
        "  source_date TEXT,"
        "  observer TEXT,"
        "  chunk_index INTEGER,"
        "  token_count INTEGER,"
        "  pinpoint TEXT"
        ")"
    )
    c.execute(
        "INSERT INTO chunks "
        "(content, source_uri, source_date, observer, "
        " chunk_index, token_count, pinpoint) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "Notwithstanding subparagraph (A), a claim shall be deemed...",
            "cortex://legal_source/rtc-63.2",
            None,
            "test",
            0,
            12,
            "f-1-B",
        ),
    )
    c.commit()
    return c


def test_resolve_pinpoint_chunk_hit() -> None:
    c = _chunks_conn()
    chunk = _resolve_pinpoint_chunk(
        c, entity_id="legal_source:rtc-63.2", pinpoint="f-1-B"
    )
    assert chunk is not None
    assert chunk["pinpoint"] == "f-1-B"
    assert chunk["content"].startswith("Notwithstanding")


def test_resolve_pinpoint_chunk_miss() -> None:
    c = _chunks_conn()
    chunk = _resolve_pinpoint_chunk(
        c, entity_id="legal_source:rtc-63.2", pinpoint="z-99"
    )
    assert chunk is None


def test_resolve_pinpoint_chunk_wrong_entity_miss() -> None:
    c = _chunks_conn()
    chunk = _resolve_pinpoint_chunk(
        c, entity_id="legal_source:rtc-1605", pinpoint="f-1-B"
    )
    assert chunk is None
