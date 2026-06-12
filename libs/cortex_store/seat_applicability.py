"""Seat-applicability vocabulary + the /boot-skills (and future /skills) seat filter.

Single enforcement seam for the canonical seat enum (derived from agent_seat config,
not hardcoded), seat-slug validation/normalization, and the default-DENY SQL clause the
boot/skills route family binds for `for_agent` filtering. Imported by routes/boot/skills.py
today; reused by the /skills HTTP endpoint (todo:skills-http-endpoint) and Track B B2.
"""

from __future__ import annotations

from fastapi import HTTPException

from agent_seat.profiles import known_seats
from agent_seat.registry import normalize_agent_slug

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
