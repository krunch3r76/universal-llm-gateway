"""Class 2 entity-prefix resolver.

Maps a bare token (e.g. ``camelia_mahmoudi``) to its canonical entity_id
(e.g. ``person:camelia-mahmoudi``) by exact-match against the substrate's
``entities.id`` column. Per Q1 (cursor dispatch packet), only exact
matches against ``entities.id`` (and ``entities.aliases`` if present)
are honored — fuzzy resolution is deferred to a future Class 7.

The resolver is split into a Protocol and a default cortex-backed
implementation so tests can inject a static dict-backed resolver and
the library has zero hard dependency on the cortex HTTP surface.
"""

from __future__ import annotations

from typing import Protocol

import httpx
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client


def bare_token_to_slug(token: str) -> str:
    """Convert a bare predicate-form arg to its slug form.

    Bare tokens use underscores between words (`camelia_mahmoudi`); entity
    slugs use hyphens (`camelia-mahmoudi`). The conversion is
    underscore→hyphen verbatim — case is preserved (case-folding is
    Class 3's responsibility). Numeric tokens pass through unchanged.
    """
    return token.replace("_", "-")


class EntityResolver(Protocol):
    """Resolve a bare-token slug to its canonical entity_id.

    Returns the full ``<type>:<slug>`` entity_id on exact match against
    ``entities.id``, or None if no entity matches.
    """

    def resolve_slug(self, slug: str) -> str | None: ...


class StaticEntityResolver:
    """In-memory resolver. Tests inject a fixed slug→entity_id map.

    Also usable as a cache layer in front of CortexEntityResolver — a
    future enhancement, not required for v1.3 acceptance.
    """

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = dict(mapping)

    def resolve_slug(self, slug: str) -> str | None:
        return self._mapping.get(slug)


# Type prefixes the cortex-backed resolver tries when looking up a bare
# slug. Empirical seed from the §7 cluster table + recent boot manifests.
# Append-only — adding a new entity type here is safe; removing one
# could regress lookups. Order is preference order for the rare case of
# slug collision across types (no collisions observed in the §10.6 Q1
# fixtures, but kept deterministic for forward stability).
_DEFAULT_TYPE_PREFIXES: tuple[str, ...] = (
    "person",
    "asset",
    "legal_matter",
    "case",
    "account",
    "document",
    "decision",
    "service",
    "todo",
    "plan_phase",
    "ai_agent",
    "tool",
    "skill",
    "rule",
    "doc",
    "firm",
    "event",
    "goal",
    "tax",
    "legal_source",
    "artifact",
    "estate",
)


class CortexEntityResolver:
    """Cortex-backed resolver via ``GET /entities/{type}:{slug}``.

    Uses the shared transport_utils factory per `[universal:transport]`
    architectural invariant — no direct httpx UDS construction. Each
    lookup tries each known type prefix in order; first 200 wins.
    Returns None when no candidate prefix yields a hit.

    Caching is a future concern; v1.3 acceptance does not require it.
    """

    def __init__(
        self,
        cortex_url: str = DEFAULT_CORTEX_URL,
        type_prefixes: tuple[str, ...] = _DEFAULT_TYPE_PREFIXES,
    ) -> None:
        self._cortex_url = cortex_url
        self._type_prefixes = type_prefixes

    def resolve_slug(self, slug: str) -> str | None:
        with make_sync_client(self._cortex_url, timeout=5.0) as client:
            for prefix in self._type_prefixes:
                candidate = f"{prefix}:{slug}"
                try:
                    resp = client.get(f"/entities/{candidate}")
                except httpx.HTTPError:
                    continue
                if resp.status_code == 200:
                    return candidate
        return None
