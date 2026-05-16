"""Unit tests for the FTS5 quote-wrap sanitizer in assertions routes."""

from __future__ import annotations

import pytest

from cortex_store.routes.assertions import _sanitize_fts_query

# ---------------------------------------------------------------------------
# Basic token handling
# ---------------------------------------------------------------------------


def test_single_plain_term() -> None:
    assert _sanitize_fts_query("hello") == '"hello"'


def test_multi_term_implicit_and() -> None:
    assert _sanitize_fts_query("foo bar") == '"foo" "bar"'


def test_empty_string_returns_empty() -> None:
    assert _sanitize_fts_query("") == ""


def test_whitespace_only_returns_empty() -> None:
    assert _sanitize_fts_query("   ") == ""


# ---------------------------------------------------------------------------
# Operator characters that broke the old enumeration sanitizer
# ---------------------------------------------------------------------------


def test_hyphen_in_term() -> None:
    # Hyphens are interpreted as NOT-minus by FTS5 outside quotes.
    result = _sanitize_fts_query("state-of-the-art")
    assert result == '"state-of-the-art"'


def test_colon_in_term() -> None:
    # Colon is a column-filter operator in FTS5.
    result = _sanitize_fts_query("key:value")
    assert result == '"key:value"'


def test_parentheses_in_query() -> None:
    result = _sanitize_fts_query("foo(bar)")
    assert result == '"foo(bar)"'


def test_caret_and_star() -> None:
    result = _sanitize_fts_query("foo^2 bar*")
    assert result == '"foo^2" "bar*"'


# ---------------------------------------------------------------------------
# Boolean keywords — the old sanitizer stripped these; quote-wrap disables them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "keyword", ["AND", "OR", "NOT", "NEAR", "and", "or", "not", "near"]
)
def test_boolean_keywords_are_quoted(keyword: str) -> None:
    result = _sanitize_fts_query(keyword)
    assert result == f'"{keyword}"'


def test_boolean_keyword_in_phrase() -> None:
    result = _sanitize_fts_query("cats AND dogs")
    assert result == '"cats" "AND" "dogs"'


# ---------------------------------------------------------------------------
# Embedded double-quote escape (FTS5 uses "" inside a phrase)
# ---------------------------------------------------------------------------


def test_embedded_double_quote_escaped() -> None:
    # Input: he"said  →  FTS5 phrase: "he""said"
    result = _sanitize_fts_query('he"said')
    assert result == '"he""said"'


def test_multiple_embedded_quotes() -> None:
    result = _sanitize_fts_query('a"b"c')
    assert result == '"a""b""c"'


def test_term_that_is_only_a_quote() -> None:
    result = _sanitize_fts_query('"')
    assert result == '""""'


# ---------------------------------------------------------------------------
# Multi-term variations
# ---------------------------------------------------------------------------


def test_multi_term_with_hyphenated_word() -> None:
    result = _sanitize_fts_query("file-path document")
    assert result == '"file-path" "document"'


def test_leading_trailing_whitespace_stripped() -> None:
    result = _sanitize_fts_query("  hello world  ")
    assert result == '"hello" "world"'


def test_extra_internal_whitespace_collapsed() -> None:
    # split() handles runs of whitespace natively.
    result = _sanitize_fts_query("foo   bar")
    assert result == '"foo" "bar"'
