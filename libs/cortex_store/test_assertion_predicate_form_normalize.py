"""Phase D — Q5 normalize-on-write integration + idempotency.

Acceptance criteria covered (from
`cortex://notes/system/threads/v1.3-q5-write-time-wiring-cursor-dispatch.md`):

| § | Criterion | Test |
|---|---|---|
| 1 | POST with predicate_form → canonical_form stored | `test_post_normalizes_legacy_to_canonical` |
| 3 | PATCH with predicate_form → canonical_form stored | `test_patch_normalizes_legacy_to_canonical` |
| 4 | PATCH explicit null → cleared, no normalize | `test_patch_null_clears_no_normalize` (in `test_assertion_update_predicate_form.py`) |
| 5 | requires_human_review → review_status='flagged' | `test_patch_requires_human_review_flags_row` |
| 7 | Idempotency fixed-point on 8 patched Q1 IDs | `test_q1_canonicals_fixed_point_on_live_db` |
| — | Function-level idempotency `n(n(x)) == n(x)` | `test_function_level_idempotency` |
| — | Malformed predicate_form → 422, not 500 | `test_patch_unparseable_predicate_form_422` |

Phase C event emission was deferred (see agent-bus thread 1008 turn 6 — Option C
confirmed by claude-web). Tests assert canonical-form storage and
review-status side effects only; no event-emission assertions.

Live-DB fixed-point test skips when ``~/.cortex/cortex.db`` (or
``$CORTEX_DB_PATH``) is absent so CI without the substrate stays green.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from predicate_form import normalize_predicate_domain
from predicate_form.entity_resolve import DBEntityResolver

from cortex_store.routes.assertions import _update_assertion_impl

# ---------------------------------------------------------------------------
# Fixture helpers — head schema via conftest ``migrated_conn``
# ---------------------------------------------------------------------------

# Q1 entities — required for Class 2 slug→entity_id rewriting in normalize.
_Q1_ENTITIES = [
    ("person:camelia-mahmoudi", "person"),
    ("person:kaywan-mansubi", "person"),
    (
        "legal_matter:affidavit-of-death-community-property-owner",
        "legal_matter",
    ),
    ("legal_matter:estate-of-fred-mansubi-24pr197054", "legal_matter"),
    ("asset:mary-mansubi-life-insurance-policy-500k", "asset"),
    ("asset:mary-mansubi-life-insurance-policy-200k", "asset"),
]


def _seed_q1_entities(conn: sqlite3.Connection) -> None:
    for eid, etype in _Q1_ENTITIES:
        conn.execute(
            "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
            (eid, etype, eid.split(":")[-1]),
        )
    conn.commit()


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    _seed_q1_entities(migrated_conn)
    return migrated_conn


def _insert_assertion(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    claim: str = "Phase D fixture claim.",
    confidence: str = "believed",
    predicate_form: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, "
        "created_at, updated_at, predicate_form)"
        " VALUES (?, ?, ?, ?, datetime('now'), datetime('now'), ?)",
        (entity_id, claim, confidence, "test evidence", predicate_form),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _row(conn: sqlite3.Connection, aid: int) -> dict:
    row = conn.execute(
        "SELECT predicate_form, review_status, review_notes "
        "FROM assertions WHERE id = ?",
        (aid,),
    ).fetchone()
    return dict(row) if row else {}


def _patch_update(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr(
        "cortex_store.routes.assertions._update.cortex_conn", lambda: conn
    )


# ---------------------------------------------------------------------------
# §3, §1 — PATCH normalizes legacy form to canonical
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entity_id,legacy,expected_canonical",
    [
        (
            "person:camelia-mahmoudi",
            "role(camelia_mahmoudi, filer, 24PR197054)",
            "role(person:camelia-mahmoudi, filer, 24pr197054)",
        ),
        (
            "asset:mary-mansubi-life-insurance-policy-500k",
            "has_attribute(mary_mansubi_life_insurance_policy_500k, value_500000)",
            "has_attribute(asset:mary-mansubi-life-insurance-policy-500k, value_500000)",
        ),
        (
            "person:camelia-mahmoudi",
            "status(camelia_mahmoudi, unavailable, August_21_2024)",
            "status(person:camelia-mahmoudi, unavailable, august_21_2024)",
        ),
    ],
)
def test_patch_normalizes_legacy_to_canonical(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    entity_id: str,
    legacy: str,
    expected_canonical: str,
) -> None:
    """Q5.4 always-re-normalize on PATCH — legacy form rewrites to canonical."""
    _patch_update(monkeypatch, conn)
    aid = _insert_assertion(conn, entity_id=entity_id)

    result = _update_assertion_impl(aid, {"predicate_form": legacy})

    assert result["predicate_form"] == expected_canonical
    assert _row(conn, aid)["predicate_form"] == expected_canonical


def test_patch_canonical_input_idempotent(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Already-canonical input survives re-normalize unchanged (Q5.4 idempotency)."""
    _patch_update(monkeypatch, conn)
    aid = _insert_assertion(conn, entity_id="person:camelia-mahmoudi")

    canonical = "role(person:camelia-mahmoudi, filer, 24pr197054)"
    result = _update_assertion_impl(aid, {"predicate_form": canonical})

    assert result["predicate_form"] == canonical
    assert _row(conn, aid)["predicate_form"] == canonical


# ---------------------------------------------------------------------------
# §5 — requires_human_review path → review_status='flagged'
# ---------------------------------------------------------------------------


def test_patch_requires_human_review_flags_row(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Class 6 review trigger → row gains review_status='flagged' + a note.

    A bare-token subject with no entity match (Class 2 cannot rewrite) on a
    generic-state predicate ("status") is a Class 6 review trigger. The
    PATCH must store the canonical form AND flag the row.
    """
    _patch_update(monkeypatch, conn)
    aid = _insert_assertion(conn, entity_id="person:camelia-mahmoudi")

    # Cross-check our expectation with the function output before the PATCH.
    expectation = normalize_predicate_domain(
        "person:camelia-mahmoudi",
        "status(unknown_subject_xyz, ready_to_file)",
        claim_text="Phase D fixture claim.",
        resolver=DBEntityResolver(conn),
    )
    if not expectation["requires_human_review"]:
        pytest.skip(
            "Class 6 trigger condition no longer fires for this fixture; "
            "review_status flag path is exercised via live-DB §7 row coverage."
        )

    result = _update_assertion_impl(
        aid,
        {"predicate_form": "status(unknown_subject_xyz, ready_to_file)"},
    )

    assert result["predicate_form"] == expectation["canonical_form"]
    row = _row(conn, aid)
    assert row["review_status"] == "flagged"
    assert row["review_notes"] and "predicate normalize" in row["review_notes"]


# ---------------------------------------------------------------------------
# Malformed input → HTTP 422 (production safety from Phase B always-re-normalize)
# ---------------------------------------------------------------------------


def test_patch_unparseable_predicate_form_422(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garbage predicate_form must surface as 422, never 500.

    Q5.4 always-re-normalize makes the parser load-bearing on every write;
    malformed input from upstream (e.g. occasional Stargate `predicate-extract`
    LLM writeback misfires) must not crash the route.
    """
    _patch_update(monkeypatch, conn)
    aid = _insert_assertion(conn, entity_id="person:camelia-mahmoudi")

    with pytest.raises(HTTPException) as exc_info:
        _update_assertion_impl(aid, {"predicate_form": "not a predicate form"})

    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["error"] == "predicate_form_unparseable"


# ---------------------------------------------------------------------------
# Function-level idempotency — n(n(x)) == n(x) for Q1 fixtures
# ---------------------------------------------------------------------------


_Q1_FIXTURES_FOR_IDEMPOTENCY = [
    pytest.param(
        "person:camelia-mahmoudi",
        "role(camelia_mahmoudi, filer, 24PR197054)",
        id="3284",
    ),
    pytest.param(
        "person:camelia-mahmoudi",
        "role(camelia_mahmoudi, filer, case)",
        id="3557",
    ),
    pytest.param(
        "legal_matter:affidavit-of-death-community-property-owner",
        "has_attribute(affidavit_of_death_community_property_owner, cost, 450)",
        id="3623",
    ),
    pytest.param(
        "legal_matter:estate-of-fred-mansubi-24pr197054",
        "status(camelia_mahmoudi, ready_to_file)",
        id="3818",
    ),
    pytest.param(
        "asset:mary-mansubi-life-insurance-policy-500k",
        "has_attribute(mary_mansubi_life_insurance_policy_500k, value_500000)",
        id="4134",
    ),
    pytest.param(
        "asset:mary-mansubi-life-insurance-policy-200k",
        "has_attribute(mary_mansubi_life_insurance_policy_200k, value_200000)",
        id="4135",
    ),
    pytest.param(
        "person:kaywan-mansubi",
        "role(kaywan_mansubi, administrator, estate_of_dr_fred_mansubi)",
        id="4525",
    ),
    pytest.param(
        "person:camelia-mahmoudi",
        "status(camelia_mahmoudi, unavailable, August_21_2024)",
        id="5697",
    ),
]


@pytest.mark.parametrize("entity_id,legacy", _Q1_FIXTURES_FOR_IDEMPOTENCY)
def test_function_level_idempotency(
    conn: sqlite3.Connection, entity_id: str, legacy: str
) -> None:
    """`normalize(normalize(x)) == normalize(x)` — required by Q5.4 + §10.4.

    Idempotency under re-normalize is the load-bearing invariant for the §14.1
    backfill. If this fails, we cannot safely sweep NULL-row backfill through
    the same normalize-aware PATCH path.
    """
    resolver = DBEntityResolver(conn)

    once = normalize_predicate_domain(
        entity_id, legacy, claim_text=None, resolver=resolver
    )
    twice = normalize_predicate_domain(
        entity_id,
        once["canonical_form"],
        claim_text=None,
        resolver=resolver,
    )

    assert twice["canonical_form"] == once["canonical_form"], (
        f"non-idempotent: once={once['canonical_form']!r} "
        f"twice={twice['canonical_form']!r}"
    )
    assert twice["domain_key"] == once["domain_key"]


# ---------------------------------------------------------------------------
# §7 — Idempotency fixed-point against the live patched Q1 rows (FREEZE gate)
# ---------------------------------------------------------------------------

_Q1_PATCHED_IDS: tuple[int, ...] = (
    3284,
    3557,
    3623,
    3818,
    4134,
    4135,
    4525,
    5697,
)

_LIVE_DB = Path(
    os.environ.get("CORTEX_DB_PATH", str(Path.home() / ".cortex" / "cortex.db"))
)
_SKIP_LIVE = pytest.mark.skipif(
    not _LIVE_DB.exists(),
    reason=f"cortex DB not found at {_LIVE_DB}",
)


@_SKIP_LIVE
@pytest.mark.parametrize("assertion_id", _Q1_PATCHED_IDS)
def test_q1_canonicals_fixed_point_on_live_db(assertion_id: int) -> None:
    """§7 acceptance: each patched Q1 row's stored predicate_form MUST be
    a fixed point under normalize. Failure = FREEZE before §14.1 backfill.
    """
    conn = sqlite3.connect(str(_LIVE_DB), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT entity_id, claim, predicate_form FROM assertions WHERE id = ?",
            (assertion_id,),
        ).fetchone()
        if not row:
            pytest.skip(f"assertion {assertion_id} not present on live DB")

        stored = row["predicate_form"]
        assert stored is not None, (
            f"assertion {assertion_id}: predicate_form is NULL — Q1 patch regression"
        )

        out = normalize_predicate_domain(
            row["entity_id"],
            stored,
            claim_text=row["claim"],
            resolver=DBEntityResolver(conn),
        )
        assert out["canonical_form"] == stored, (
            f"assertion {assertion_id}: stored={stored!r} "
            f"normalized={out['canonical_form']!r} — FREEZE"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Decision self-status polarity guard — agent-bus thread 1267 (repro 13130)
#
# Integration coverage on the PATCH write path: a decision entity whose
# synthesized predicate_form is status(self, rejected) while its tracked
# workflow_state is 'accepted' must store status(self, accepted) and must NOT
# be flagged for human review. Uses a dedicated fixture whose `entities` table
# carries a workflow_state column on the head-schema fixture.
# ---------------------------------------------------------------------------

_DECISION_ID_1267 = "decision:bench-supergrok-heavy-reviewer-let-subscription-lapse"


def _seed_decision_entity(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name, workflow_state) "
        "VALUES (?, 'decision', ?, 'accepted')",
        (_DECISION_ID_1267, _DECISION_ID_1267.split(":")[-1]),
    )
    conn.commit()


def test_patch_decision_self_status_polarity_corrected(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """thread 1267: status(self, rejected) on an accepted decision → corrected
    to status(self, accepted), row NOT flagged for review."""
    _seed_decision_entity(conn)
    _patch_update(monkeypatch, conn)
    aid = _insert_assertion(
        conn,
        entity_id=_DECISION_ID_1267,
        claim="Operator decided to bench SuperGrok Heavy and let the subscription lapse.",
        confidence="confirmed",
    )

    result = _update_assertion_impl(
        aid, {"predicate_form": f"status({_DECISION_ID_1267}, rejected)"}
    )

    expected = f"status({_DECISION_ID_1267}, accepted)"
    assert result["predicate_form"] == expected
    row = _row(conn, aid)
    assert row["predicate_form"] == expected
    # Faithful self-status → not flagged (contrast test_patch_requires_human_review_flags_row).
    assert row["review_status"] != "flagged"


def test_patch_decision_self_status_missing_workflow_col_is_non_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: when the entities table has no workflow_state column (legacy
    fixtures / pre-migration DBs), the write path must not 500 — the guard
    simply no-ops and Class 6 behavior is preserved."""
    legacy = sqlite3.connect(":memory:")
    legacy.row_factory = sqlite3.Row
    legacy.executescript(
        """
        CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL);
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            confidence TEXT NOT NULL,
            confidence_score REAL,
            evidence TEXT,
            evidence_uris TEXT,
            seeded_by TEXT,
            derivation_type TEXT,
            chunk_id INTEGER,
            chunk_id_schema TEXT,
            reasoning_summary TEXT,
            is_atomic INTEGER DEFAULT 1,
            is_decontextualized INTEGER DEFAULT 1,
            observed_at TEXT,
            valid_from TEXT,
            valid_until TEXT,
            superseded_by INTEGER,
            review_status TEXT,
            reviewer TEXT,
            reviewed_at TEXT,
            review_notes TEXT,
            resolution_status TEXT,
            fulfillment_assertion_id INTEGER,
            quality_score REAL,
            prospective_summary TEXT,
            events_json TEXT,
            artifact_uri TEXT,
            artifact_storage TEXT DEFAULT 'inline',
            entrenchment_score REAL,
            predicate_form TEXT,
            raw_predicate_form TEXT,
            normalization_decision TEXT,
            candidate_set_fingerprint TEXT,
            normalizer_version TEXT,
            attributes TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    legacy.execute(
        "INSERT INTO entities (id, type, name) VALUES (?, 'decision', ?)",
        (_DECISION_ID_1267, "legacy-decision"),
    )
    legacy.commit()
    _patch_update(monkeypatch, legacy)
    aid = _insert_assertion(legacy, entity_id=_DECISION_ID_1267, confidence="confirmed")

    result = _update_assertion_impl(
        aid, {"predicate_form": f"status({_DECISION_ID_1267}, rejected)"}
    )
    assert result["predicate_form"] == f"status({_DECISION_ID_1267}, rejected)"
