"""Seat-applicability vocabulary for skill/rule entity writes and discovery params.

Canonical seat enum (derived from agent_seat config), seat-slug validation/normalization,
and capability filtering for GET /skills. ``applicable_agents`` is informational metadata
only — discovery does not filter on it.
"""

from __future__ import annotations

import json

from agent_seat.profiles import (
    CAPABILITY_TOKENS,
    known_seats,
    seat_capability_map,
)
from agent_seat.registry import normalize_agent_slug
from fastapi import HTTPException

UNIVERSAL = "*"

SCOPE_TOKENS = frozenset({"universal", "ecosystem", "ulg"})


def validate_scope(attributes: dict[str, object] | None) -> None:
    """Reject an entity write whose scope holds an unknown token."""
    if not attributes:
        return
    scope = attributes.get("scope")
    if scope is None:
        return
    if not isinstance(scope, str) or scope not in SCOPE_TOKENS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown scope {scope!r}; expected one of {sorted(SCOPE_TOKENS)}."
            ),
        )


CAPABILITY_CLAUSE = """
    AND NOT EXISTS (
        SELECT 1 FROM json_each(json_extract(attributes, '$.capabilities_required'))
        WHERE value NOT IN (SELECT value FROM json_each(?))
    )
"""


def canonical_seat_or_422(slug: str) -> str:
    """Normalize a caller-supplied seat slug and validate it against the registry.

    Returns the canonical {family}-{platform} slug (or '*'). Raises HTTP 422 when the
    slug does not resolve to a registered seat or the universal token.
    """
    if slug == UNIVERSAL:
        return UNIVERSAL
    canonical = normalize_agent_slug(slug)
    if canonical not in known_seats():
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown seat slug {slug!r} (normalized {canonical!r}); expected one "
                f"of {sorted(known_seats())} or '*'."
            ),
        )
    return canonical


def seat_capabilities_json(seat: str) -> str:
    """JSON array of capability tokens for the seat (CAPABILITY_CLAUSE bind value)."""
    toks = seat_capability_map().get(seat, frozenset())
    return json.dumps(sorted(toks))


def for_agent_filter_clause(canonical_seat: str) -> str:
    """Seat filter for skill discovery — retired; ``applicable_agents`` is metadata only."""
    del canonical_seat
    return ""


def validate_applicable_agents(attributes: dict[str, object] | None) -> None:
    """Reject an entity write whose applicable_agents is not a JSON list."""
    if not attributes:
        return
    agents = attributes.get("applicable_agents")
    if agents is None:
        return
    if not isinstance(agents, list):
        raise HTTPException(
            status_code=422, detail="applicable_agents must be a JSON list"
        )


def validate_capabilities_required(attributes: dict[str, object] | None) -> None:
    """Reject capability tokens outside the closed enum."""
    if not attributes:
        return
    required = attributes.get("capabilities_required")
    if required is None:
        return
    if not isinstance(required, list):
        raise HTTPException(
            status_code=422, detail="capabilities_required must be a JSON list"
        )
    for token in required:
        if str(token) not in CAPABILITY_TOKENS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown capability token {token!r}; "
                    f"expected one of {sorted(CAPABILITY_TOKENS)}."
                ),
            )
