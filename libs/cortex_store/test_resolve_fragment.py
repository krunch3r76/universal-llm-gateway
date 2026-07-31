"""Unit tests for the cortex:// URI fragment extension (spec § 2.2).

Covers:
  * ``parse_cortex_uri`` extracts the fragment as ``pinpoint``.
  * No fragment → ``pinpoint`` is None (back-compat with pre-spec URIs).
  * Fragment + revision query both parse independently.
  * ``_resolve_pinpoint_chunk`` returns None after Phase E dropped chunk lookup
    (migration 040 stub — pinpoint resolution deferred to Phase F+).
"""

from __future__ import annotations

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


def test_resolve_pinpoint_chunk_hit() -> None:
    chunk = _resolve_pinpoint_chunk("legal_source:rtc-63.2", "f-1-B")
    assert chunk is None


def test_resolve_pinpoint_chunk_miss() -> None:
    chunk = _resolve_pinpoint_chunk("legal_source:rtc-63.2", "z-99")
    assert chunk is None


def test_resolve_pinpoint_chunk_wrong_entity_miss() -> None:
    chunk = _resolve_pinpoint_chunk("legal_source:rtc-1605", "f-1-B")
    assert chunk is None
