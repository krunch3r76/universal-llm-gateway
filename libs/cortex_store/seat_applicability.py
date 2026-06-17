"""Seat-applicability vocabulary + the GET /skills?view=boot seat filter.

Single enforcement seam for the canonical seat enum (derived from agent_seat config,
not hardcoded), seat-slug validation/normalization, and the default-DENY SQL clause the
GET /skills?view=boot route binds for `for_agent` filtering. Reused by the /skills HTTP
endpoint and Track B B2.
"""

from __future__ import annotations

import json

from agent_seat.profiles import (
    CAPABILITY_TOKENS,
    known_seats,
    load_profiles,
    seat_capability_map,
)
from agent_seat.registry import normalize_agent_slug
from fastapi import HTTPException

UNIVERSAL = "*"

# Default-DENY: a skill with no `applicable_agents` attribute matches NO seat. Universal
# visibility requires an explicit ['*']. The IS NOT NULL guard makes the deny explicit and
# avoids relying on json_each(NULL) behaviour (implementation-defined; can raise on bad JSON).
FOR_AGENT_CLAUSE = """
    AND json_extract(attributes, '$.applicable_agents') IS NOT NULL
    AND EXISTS (
        SELECT 1 FROM json_each(json_extract(attributes, '$.applicable_agents'))
        WHERE value IN ('*', ?)
    )
"""

# API seats: universal ``*`` does not imply visibility — explicit seat slug only.
FOR_AGENT_EXPLICIT_CLAUSE = """
    AND json_extract(attributes, '$.applicable_agents') IS NOT NULL
    AND EXISTS (
        SELECT 1 FROM json_each(json_extract(attributes, '$.applicable_agents'))
        WHERE value = ?
    )
"""

_EXPLICIT_ONLY_PLATFORMS = frozenset({"api", "api-multi"})

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
    """Seat filter for skill discovery: cursor/web inherit universal ``*``; api explicit only."""
    parts = canonical_seat.split("-", 1)
    if len(parts) == 2:
        profile = load_profiles().get((parts[0], parts[1]))
        if profile is not None and profile.platform in _EXPLICIT_ONLY_PLATFORMS:
            return FOR_AGENT_EXPLICIT_CLAUSE
    return FOR_AGENT_CLAUSE


def validate_applicable_agents(attributes: dict[str, object] | None) -> None:
    """Reject an entity write whose applicable_agents holds an unknown seat slug."""
    if not attributes:
        return
    agents = attributes.get("applicable_agents")
    if agents is None:
        return
    if not isinstance(agents, list):
        raise HTTPException(
            status_code=422, detail="applicable_agents must be a JSON list"
        )
    for slug in agents:
        canonical_seat_or_422(str(slug))


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
