"""HTTP If-Match parsing for concern position CAS (RFC 9110 subset)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_STRONG_ETAG = re.compile(r'^"(?:\\.|[^"\\])*"$')
_WEAK_PREFIX = re.compile(r"^\s*W/\s*", re.IGNORECASE)


@dataclass(frozen=True)
class IfMatchResult:
    expected_revision: int | None
    wildcard: bool = False


class IfMatchParseError(ValueError):
    """Malformed If-Match header."""


def parse_if_match(header: str | None, *, current_revision: int) -> IfMatchResult:
    """Parse ``If-Match`` into expected revision or wildcard."""
    if header is None or not str(header).strip():
        raise IfMatchParseError("missing")
    text = str(header).strip()
    if text == "*":
        if current_revision == 0:
            raise IfMatchParseError("wildcard_on_empty")
        return IfMatchResult(expected_revision=None, wildcard=True)
    tags = [part.strip() for part in text.split(",") if part.strip()]
    if not tags:
        raise IfMatchParseError("missing")
    revisions: list[int] = []
    for tag in tags:
        if _WEAK_PREFIX.match(tag):
            raise IfMatchParseError("weak")
        if not _STRONG_ETAG.match(tag):
            raise IfMatchParseError("malformed")
        inner = tag[1:-1]
        if not inner.isdigit():
            raise IfMatchParseError("malformed")
        revisions.append(int(inner))
    if len(revisions) == 1:
        return IfMatchResult(expected_revision=revisions[0])
    if current_revision in revisions:
        return IfMatchResult(expected_revision=current_revision)
    return IfMatchResult(expected_revision=revisions[0])
