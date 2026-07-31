"""Predicate-form parser tests — round-trip + edge cases."""

from __future__ import annotations

import pytest
from predicate_form.parser import (
    Predicate,
    PredicateParseError,
    parse,
    unparse,
)


@pytest.mark.parametrize(
    "s",
    [
        "role(person:camelia-mahmoudi, filer, 24pr197054)",
        "status(camelia_mahmoudi, ready_to_file)",
        "has_attribute(legal_matter:foo, cost, 450)",
        "is_a(asset:bar, authoritative_version)",
    ],
)
def test_parse_unparse_round_trip(s: str) -> None:
    assert unparse(parse(s)) == s


def test_parse_no_args() -> None:
    p = parse("ping()")
    assert p == Predicate("ping", ())
    assert unparse(p) == "ping()"


def test_parse_strips_arg_whitespace() -> None:
    assert parse("foo(  a , b  )") == Predicate("foo", ("a", "b"))


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "no_parens",
        "missing_close(",
        "missing_open)",
        "(no_name)",
        "foo(a, , b)",
        "nested(foo(bar))",
    ],
)
def test_parse_rejects_malformed(bad: str) -> None:
    with pytest.raises(PredicateParseError):
        parse(bad)
