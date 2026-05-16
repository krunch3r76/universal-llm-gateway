"""Predicate-form parser.

Grammar (per v4 §4.1):
    predicate_form := name '(' arglist? ')'
    arglist        := arg (',' arg)*
    arg            := token

Tokens are everything between commas / parens, with surrounding whitespace
stripped. Arguments themselves do not contain nested parens or commas in
the corpus the substrate has accumulated to date — if a future fixture
violates that we'll surface it explicitly rather than silently accept.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Predicate:
    """Parsed predicate_form."""

    name: str
    args: tuple[str, ...]


class PredicateParseError(ValueError):
    """Raised when a string is not a well-formed predicate_form."""


def parse(s: str) -> Predicate:
    """Parse a predicate_form string into a Predicate AST.

    Raises PredicateParseError for malformed input. Whitespace around
    args is stripped; whitespace inside an arg token is preserved.
    """
    text = s.strip()
    if not text:
        raise PredicateParseError("empty predicate_form")

    open_idx = text.find("(")
    if open_idx < 0 or not text.endswith(")"):
        raise PredicateParseError(f"missing parens: {s!r}")

    name = text[:open_idx].strip()
    if not name:
        raise PredicateParseError(f"missing predicate name: {s!r}")

    inner = text[open_idx + 1 : -1].strip()
    if not inner:
        return Predicate(name=name, args=())

    # No nested-paren / quoted-comma support — corpus does not need it.
    if "(" in inner or ")" in inner:
        raise PredicateParseError(f"nested parens not supported in args: {s!r}")

    args = tuple(part.strip() for part in inner.split(","))
    if any(a == "" for a in args):
        raise PredicateParseError(f"empty arg in {s!r}")
    return Predicate(name=name, args=args)


def unparse(p: Predicate) -> str:
    """Serialize a Predicate back to canonical predicate_form string."""
    return f"{p.name}({', '.join(p.args)})"
