"""CONSUMED turn grammar and unconsumed ruling detection (operational receipts v1)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

CONSUMED_TYPE_RE = re.compile(r"TYPE:\s*CONSUMED\b", re.IGNORECASE)
RULING_TYPE_RES = re.compile(r"TYPE:\s*(?:DIRECTIVE|RULING)\b", re.IGNORECASE)

_VALID_DISPOSITIONS = frozenset({"acted", "declined", "deferred"})

CONSUMPTION_MISSING_PREFIX = "CONSUMPTION_MISSING:"


def _first_type_line(body: str) -> str | None:
    """Return the first ``TYPE:`` line in *body*, or None when absent."""
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("TYPE:"):
            return stripped
    return None


def _first_type_matches(body: str, pattern: re.Pattern[str]) -> bool:
    line = _first_type_line(body)
    if line is None:
        return False
    return pattern.search(line) is not None


def _is_ruling_body(body: str) -> bool:
    return _first_type_matches(body, RULING_TYPE_RES)


def _is_consumed_body(body: str) -> bool:
    return _first_type_matches(body, CONSUMED_TYPE_RE)


def parse_consumed(body: str) -> dict[str, Any] | None:
    """Parse a ``TYPE: CONSUMED`` body into ``consumed_turn``, ``disposition``, ``by``.

    Malformed bodies, missing fields, or invalid disposition return ``None``; never
    raises.
    """
    if not _is_consumed_body(body):
        return None
    fields: dict[str, str] = {}
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.upper().startswith("TYPE:"):
            continue
        if ":" not in stripped:
            return None
        key, _, value = stripped.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if not key:
            return None
        fields[key] = value
    for required in ("consumed_turn", "disposition", "by"):
        if required not in fields or not fields[required]:
            return None
    try:
        consumed_turn = int(fields["consumed_turn"])
    except ValueError:
        return None
    disposition = fields["disposition"].lower()
    if disposition not in _VALID_DISPOSITIONS:
        return None
    return {
        "consumed_turn": consumed_turn,
        "disposition": disposition,
        "by": fields["by"],
    }


def find_unconsumed_rulings(turns: Iterable[Any]) -> list[int]:
    """Return ruling turn numbers with no later ``TYPE: CONSUMED`` referencing them.

    *turns* entries are mappings with optional ``turn_number`` and ``body``. Missing
    keys are skipped; malformed bodies are ignored. Never raises.
    """
    ordered: list[tuple[int, str]] = []
    for item in turns:
        if not isinstance(item, dict):
            continue
        raw_n = item.get("turn_number")
        if raw_n is None:
            continue
        try:
            n = int(raw_n)
        except (TypeError, ValueError):
            continue
        ordered.append((n, str(item.get("body") or "")))
    ordered.sort(key=lambda pair: pair[0])

    ruling_nums = [n for n, body in ordered if _is_ruling_body(body)]
    if not ruling_nums:
        return []

    unconsumed: list[int] = []
    for ruling_n in ruling_nums:
        satisfied = False
        for turn_n, body in ordered:
            if turn_n <= ruling_n:
                continue
            if not _is_consumed_body(body):
                continue
            parsed = parse_consumed(body)
            if parsed is not None and parsed.get("consumed_turn") == ruling_n:
                satisfied = True
                break
        if not satisfied:
            unconsumed.append(ruling_n)
    return unconsumed


def consumption_missing_marker(turn_numbers: list[int]) -> str | None:
    """Body marker ``CONSUMPTION_MISSING:n[,n…]`` or ``None`` when *turn_numbers* is empty."""
    if not turn_numbers:
        return None
    nums = sorted({int(n) for n in turn_numbers})
    return f"{CONSUMPTION_MISSING_PREFIX}{','.join(str(n) for n in nums)}"
