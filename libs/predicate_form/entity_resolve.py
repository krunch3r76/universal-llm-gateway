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

import hashlib
import sqlite3
from typing import NamedTuple, Protocol

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


class ResolutionResult(NamedTuple):
    """Cardinality-aware resolution output.

    decision: one of 'resolved_single', 'no_match', 'collision_refused'.
    match: the chosen entity_id when decision='resolved_single', else None.
        For shadow-mode compat with current resolve_slug, on 'collision_refused'
        the first-match entity_id is returned in `match_first_match` so callers
        that want existing behavior can read it; new ledger writers MUST NOT
        use match_first_match as the canonical resolution.
    candidates: full sorted list of entity_ids matched (length 0, 1, or >1).
    candidate_fingerprint: SHA256(first 16) of '\n'.join(sorted(candidates)),
        or '' when candidates is empty.
    """

    decision: str
    match: str | None
    match_first_match: str | None
    candidates: tuple[str, ...]
    candidate_fingerprint: str


class EntityResolver(Protocol):
    """Resolve a bare-token slug to its canonical entity_id.

    Returns the full ``<type>:<slug>`` entity_id on exact match against
    ``entities.id``, or None if no entity matches.
    """

    def resolve_slug(self, slug: str) -> str | None: ...

    def resolve_slug_with_cardinality(self, slug: str) -> ResolutionResult: ...


class StaticEntityResolver:
    """In-memory resolver. Tests inject a fixed slug→entity_id map.

    Also usable as a cache layer in front of CortexEntityResolver — a
    future enhancement, not required for v1.3 acceptance.
    """

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = dict(mapping)

    def resolve_slug(self, slug: str) -> str | None:
        return self._mapping.get(slug)

    def resolve_slug_with_cardinality(self, slug: str) -> ResolutionResult:
        """Shadow cardinality for static test maps (bare-slug → full entity_id).

        Since static maps use bare slugs as keys (no duplicate keys possible),
        collision cases are exercised via DBEntityResolver / Cortex in tests.
        This impl returns 0- or 1-candidate results matching the dict content.
        """
        if slug in self._mapping:
            match = self._mapping[slug]
            cands: tuple[str, ...] = (match,)
            fp = _candidate_fingerprint(cands)
            return ResolutionResult("resolved_single", match, match, cands, fp)
        else:
            return ResolutionResult(
                "no_match", None, None, (), _candidate_fingerprint(())
            )


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

    def resolve_slug_with_cardinality(self, slug: str) -> ResolutionResult:
        """Enumerate *all* type prefixes (shadow cardinality); do not early-exit.

        Collects the full candidate set for ledger decision + fingerprint.
        resolve_slug (first-match) remains the storage path for canonical_form.
        """
        candidates: list[str] = []
        with make_sync_client(self._cortex_url, timeout=5.0) as client:
            for prefix in self._type_prefixes:
                candidate = f"{prefix}:{slug}"
                try:
                    resp = client.get(f"/entities/{candidate}")
                except httpx.HTTPError:
                    continue
                if resp.status_code == 200:
                    candidates.append(candidate)
        cands = tuple(sorted(candidates))
        fp = _candidate_fingerprint(cands)
        n = len(cands)
        if n == 0:
            return ResolutionResult("no_match", None, None, cands, fp)
        if n == 1:
            m = cands[0]
            return ResolutionResult("resolved_single", m, m, cands, fp)
        # >=2: collision_refused; first-match preserved in match_first_match for shadow compat
        return ResolutionResult("collision_refused", None, cands[0], cands, fp)


class DBEntityResolver:
    """In-process entity resolver for use inside the cortex-api routes.

    Mirrors CortexEntityResolver's resolution semantics via direct SQLite
    reads instead of HTTP roundtrips, eliminating the loopback cost when
    normalize_predicate_domain() runs inside the API process itself.

    Resolution strategy: tries each known type prefix in ``_DEFAULT_TYPE_PREFIXES``
    order, returning the first ``<type>:<slug>`` string that matches
    ``entities.id`` exactly. No alias lookup — identical semantics to the
    HTTP resolver's GET-by-ID path.

    Per Q5.3 decision (b): this resolver is for in-process use only.
    External callers (agents, pipelines) continue to use CortexEntityResolver.

    ∀ DBEntityResolver(conn): conn must be a live sqlite3.Connection whose
    ``entities`` table is accessible. The connection is not owned here — the
    caller is responsible for its lifecycle.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        type_prefixes: tuple[str, ...] = _DEFAULT_TYPE_PREFIXES,
    ) -> None:
        self._conn = conn
        self._type_prefixes = type_prefixes

    def resolve_slug(self, slug: str) -> str | None:
        for prefix in self._type_prefixes:
            candidate = f"{prefix}:{slug}"
            row = self._conn.execute(
                "SELECT id FROM entities WHERE id = ?", (candidate,)
            ).fetchone()
            if row:
                return candidate
        return None

    def resolve_slug_with_cardinality(self, slug: str) -> ResolutionResult:
        """Enumerate all prefixes via direct DB query (shadow cardinality path)."""
        candidates: list[str] = []
        for prefix in self._type_prefixes:
            candidate = f"{prefix}:{slug}"
            row = self._conn.execute(
                "SELECT id FROM entities WHERE id = ?", (candidate,)
            ).fetchone()
            if row:
                candidates.append(candidate)
        cands = tuple(sorted(candidates))
        fp = _candidate_fingerprint(cands)
        n = len(cands)
        if n == 0:
            return ResolutionResult("no_match", None, None, cands, fp)
        if n == 1:
            m = cands[0]
            return ResolutionResult("resolved_single", m, m, cands, fp)
        return ResolutionResult("collision_refused", None, cands[0], cands, fp)


def _candidate_fingerprint(candidates: tuple[str, ...]) -> str:
    """SHA256 of '\n'.join(sorted(cands)) , first 16 hex chars. '' for empty."""
    if not candidates:
        return ""
    joined = "\n".join(sorted(candidates))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
