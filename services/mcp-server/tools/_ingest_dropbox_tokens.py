"""Cross-check token extraction (plan ingest-pdf-v1 D5)."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Any

_COMMA_IN_NUMERAL = re.compile(r"(?<=\d),(?=\d{3})")
_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_MONEY_RE = re.compile(r"\$\s?\d[\d,]*\.\d{2}")
_ID_RE = re.compile(r"\b(?=[A-Z0-9]*\d)[A-Z0-9]{5,}\b")
_NUMERAL_RE = re.compile(r"\d{2,}")


def collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def _date_token(match: re.Match[str]) -> str:
    month_s, day_s, year_s = match.group(1), match.group(2), match.group(3)
    year = int(year_s)
    if len(year_s) == 2:
        year = 2000 + year
    try:
        return date(year, int(month_s), int(day_s)).isoformat()
    except ValueError:
        return match.group(0)


def _money_cents(raw: str) -> str:
    digits = re.sub(r"[^\d.]", "", raw)
    if "." not in digits:
        return digits
    whole, frac = digits.split(".", 1)
    return f"{whole}{frac[:2].ljust(2, '0')}"


def extract_token_multiset(body: str) -> Counter[str]:
    """Return token multiset for cross-check comparison."""
    text = _COMMA_IN_NUMERAL.sub("", collapse_whitespace(body))
    tokens: list[str] = []
    for match in _DATE_RE.finditer(text):
        tokens.append(_date_token(match))
    for match in _MONEY_RE.finditer(text):
        tokens.append(f"money:{_money_cents(match.group(0))}")
    for match in _ID_RE.finditer(text):
        tokens.append(f"id:{match.group(0)}")
    for match in _NUMERAL_RE.finditer(text):
        tokens.append(f"num:{match.group(0)}")
    return Counter(tokens)


def compare_bodies(
    canonical_body: str,
    check_body: str,
) -> tuple[bool, int, list[dict[str, Any]]]:
    """Agree when multisets match and at least one token compared."""
    left = extract_token_multiset(canonical_body)
    right = extract_token_multiset(check_body)
    compared = sum(left.values())
    if left == right and compared >= 1:
        return True, compared, []
    mismatches: list[dict[str, Any]] = []
    keys = sorted(set(left) | set(right))
    for key in keys:
        if left.get(key, 0) != right.get(key, 0):
            kind = key.split(":", 1)[0] if ":" in key else "numeral"
            mismatches.append(
                {
                    "kind": kind,
                    "canonical_token": key,
                    "check_token": key,
                },
            )
    return False, compared, mismatches
