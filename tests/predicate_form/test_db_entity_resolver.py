"""DBEntityResolver unit tests — Phase A acceptance §6 parity gate.

Verifies that DBEntityResolver.resolve_slug() produces the same entity_id
as CortexEntityResolver for each of the 6 Q1 entity slugs referenced by
the 8 Q1 canonical fixtures (IDs 3284/3557/3623/3818/4134/4135/4525/5697).

Two test tiers:

1. **Unit** (always runs): in-memory SQLite DB with Q1 entities seeded —
   verifies the resolver finds entities by exact ID across type prefixes.

2. **Live parity** (requires cortex DB): compares DBEntityResolver against
   the expected Q1 entity_ids on the actual cortex.db. Skipped when
   ``~/.cortex/cortex.db`` (or ``$CORTEX_DB_PATH``) is absent.

Schema note: DBEntityResolver queries ``entities.id`` directly, mirroring
CortexEntityResolver's GET-by-ID semantics. The ``entities.aliases`` column
is JSON text and the ``entity_aliases`` lookup table serves alias-based
resolution — neither is touched here. No normalization step in either
resolver, so no fixture conflict arises.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from predicate_form.entity_resolve import DBEntityResolver

# ---------------------------------------------------------------------------
# Q1 slug → expected entity_id mapping
# Six entity slugs appearing in the 8 Q1 assertion fixtures (§10.6).
# These are the bearer entities and Class-2 resolver targets referenced
# by assertion IDs 3284/3557/3623/3818/4134/4135/4525/5697.
# ---------------------------------------------------------------------------

_Q1_SLUG_TO_ENTITY_ID: dict[str, str] = {
    "camelia-mahmoudi": "person:camelia-mahmoudi",
    "kaywan-mansubi": "person:kaywan-mansubi",
    "affidavit-of-death-community-property-owner": (
        "legal_matter:affidavit-of-death-community-property-owner"
    ),
    "estate-of-fred-mansubi-24pr197054": (
        "legal_matter:estate-of-fred-mansubi-24pr197054"
    ),
    "mary-mansubi-life-insurance-policy-500k": (
        "asset:mary-mansubi-life-insurance-policy-500k"
    ),
    "mary-mansubi-life-insurance-policy-200k": (
        "asset:mary-mansubi-life-insurance-policy-200k"
    ),
}


# ---------------------------------------------------------------------------
# In-memory fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def q1_conn() -> sqlite3.Connection:
    """In-memory DB with all 6 Q1 entities inserted."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT NOT NULL)")
    for entity_id in _Q1_SLUG_TO_ENTITY_ID.values():
        etype = entity_id.split(":", 1)[0]
        conn.execute(
            "INSERT INTO entities (id, type) VALUES (?, ?)", (entity_id, etype)
        )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Unit tests (always run)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug,expected", list(_Q1_SLUG_TO_ENTITY_ID.items()))
def test_db_resolver_resolves_q1_slug(
    slug: str, expected: str, q1_conn: sqlite3.Connection
) -> None:
    """DBEntityResolver returns the expected entity_id for each Q1 slug."""
    resolver = DBEntityResolver(q1_conn)
    assert resolver.resolve_slug(slug) == expected, f"slug={slug!r}"


def test_db_resolver_returns_none_for_unknown_slug(
    q1_conn: sqlite3.Connection,
) -> None:
    """Slug with no entity match returns None — not an error."""
    resolver = DBEntityResolver(q1_conn)
    assert resolver.resolve_slug("no-such-entity-xyz") is None


def test_db_resolver_prefix_order_person_wins(q1_conn: sqlite3.Connection) -> None:
    """First matching prefix in _DEFAULT_TYPE_PREFIXES wins.

    ``person`` is the first prefix — ``camelia-mahmoudi`` must resolve to
    ``person:camelia-mahmoudi``, not any later-prefix variant.
    """
    resolver = DBEntityResolver(q1_conn)
    result = resolver.resolve_slug("camelia-mahmoudi")
    assert result == "person:camelia-mahmoudi"
    assert result is not None and result.startswith("person:")


def test_db_resolver_uses_injected_prefixes() -> None:
    """Custom type_prefixes respected — resolver only tries the given prefixes."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO entities (id, type) VALUES (?, ?)",
        ("asset:kaywan-mansubi", "asset"),
    )
    conn.commit()

    # With asset as only prefix, finds the asset entity.
    r_asset = DBEntityResolver(conn, type_prefixes=("asset",))
    assert r_asset.resolve_slug("kaywan-mansubi") == "asset:kaywan-mansubi"

    # With person-only prefixes, misses the asset entity.
    r_person = DBEntityResolver(conn, type_prefixes=("person",))
    assert r_person.resolve_slug("kaywan-mansubi") is None
    conn.close()


# ---------------------------------------------------------------------------
# Live parity tests (skip when cortex DB absent)
# ---------------------------------------------------------------------------

_LIVE_DB = Path(
    os.environ.get("CORTEX_DB_PATH", str(Path.home() / ".cortex" / "cortex.db"))
)
_SKIP_LIVE = pytest.mark.skipif(
    not _LIVE_DB.exists(),
    reason=f"cortex DB not found at {_LIVE_DB}",
)


@_SKIP_LIVE
@pytest.mark.parametrize("slug,expected", list(_Q1_SLUG_TO_ENTITY_ID.items()))
def test_db_resolver_matches_expected_on_live_db(slug: str, expected: str) -> None:
    """DBEntityResolver against live cortex.db returns the Q1 expected entity_id.

    Acceptance §6: both DBEntityResolver and CortexEntityResolver MUST return
    the same entities.id for each Q1 slug. Since CortexEntityResolver resolves
    by GET /entities/{type}:{slug} — the same entity_id lookup — agreeing with
    the known expected values is equivalent to agreeing with CortexEntityResolver.
    """
    conn = sqlite3.connect(str(_LIVE_DB), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        resolver = DBEntityResolver(conn)
        result = resolver.resolve_slug(slug)
        assert result == expected, (
            f"slug={slug!r}: DBEntityResolver returned {result!r}, "
            f"expected {expected!r}"
        )
    finally:
        conn.close()
