"""Supersede ↔ create validate_assertion parity (hard-reject-only, shadow-gated).

Covers AC1–AC14 from workspaces://universal-llm-gateway/tasks/specs/
supersede-validate-assertion-parity.md.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, status

from cortex_store.assertion_quality import DERIVATION_TYPE_TAXONOMY, validate_assertion
from cortex_store.models import AssertionCreate
from cortex_store.routes.assertions import (
    _create_assertion_impl,
    _supersede_assertion_impl,
)

_TEST_ENTITY = "test:entity"
_BASE_SUPERSEDE: dict[str, object] = {
    "entity_id": _TEST_ENTITY,
    "claim": "Replacement claim.",
    "confidence": "believed",
    "evidence": "unit test",
    "session_id": "test-session",
    "agent": "test",
}


class _NoCloseConn:
    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def close(self) -> None:
        return None


class _NoOpThread:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def start(self) -> None:
        return None


@pytest.fixture
def supersede_env(
    monkeypatch: pytest.MonkeyPatch, migrated_conn: sqlite3.Connection
) -> sqlite3.Connection:
    conn = migrated_conn
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'test', ?)",
        (_TEST_ENTITY, "test-entity"),
    )
    conn.commit()
    wrapper = _NoCloseConn(conn)

    prefix = "cortex_store.routes.assertions._supersede"
    monkeypatch.setattr(f"{prefix}.cortex_conn", lambda: wrapper)
    monkeypatch.setattr(f"{prefix}.enrich_background", lambda *a, **kw: None)
    monkeypatch.setattr(f"{prefix}.enrich_old_assertion_events", lambda *a, **kw: None)
    monkeypatch.setattr(f"{prefix}.reindex_assertion_fts", lambda *a, **kw: None)
    monkeypatch.setattr(f"{prefix}._embed_assertion_background", lambda *a, **kw: None)
    monkeypatch.setattr(f"{prefix}.threading.Thread", _NoOpThread)
    monkeypatch.setattr(
        f"{prefix}.analyze_assertion_impact",
        lambda *a, **kw: type(
            "I", (), {"likely_supersedes": [], "touched_assertions": []}
        )(),
    )
    monkeypatch.setattr(f"{prefix}.compute_entrenchment", lambda **kw: 0.5)
    monkeypatch.setattr(
        f"{prefix}.dispatch_predicate_extract_background", lambda *a, **kw: None
    )

    class _FakeVS:
        @staticmethod
        def is_initialized() -> bool:
            return False

        @staticmethod
        def delete_assertion_embedding(_id: int) -> None:
            return None

    monkeypatch.setattr(f"{prefix}.vector_store", _FakeVS)

    create_prefix = "cortex_store.routes.assertions._create"
    monkeypatch.setattr(f"{create_prefix}.cortex_conn", lambda: wrapper)
    monkeypatch.setattr(f"{create_prefix}.enrich_background", lambda *a, **kw: None)
    monkeypatch.setattr(f"{create_prefix}.reindex_assertion_fts", lambda *a, **kw: None)
    monkeypatch.setattr(f"{create_prefix}._embed_assertion_background", lambda *a, **kw: None)
    monkeypatch.setattr(f"{create_prefix}.threading.Thread", _NoOpThread)
    monkeypatch.setattr(
        f"{create_prefix}.guard_assertion_write",
        lambda *a, **kw: type(
            "G",
            (),
            {
                "allowed": True,
                "block_detail": "",
                "review_status": None,
                "contradiction_warnings": [],
            },
        )(),
    )
    monkeypatch.setattr(
        f"{create_prefix}.check_contradictions",
        lambda *a, **kw: type("C", (), {"flagged": False})(),
    )
    monkeypatch.setattr(f"{create_prefix}.check_near_duplicate", lambda *a, **kw: None)
    monkeypatch.setattr(f"{create_prefix}.record_near_duplicate", lambda *a, **kw: None)
    monkeypatch.setattr(
        f"{create_prefix}.dispatch_predicate_extract_background", lambda *a, **kw: None
    )
    return conn


def _insert_parent(
    conn: sqlite3.Connection,
    *,
    claim: str = "Parent claim.",
    derivation_type: str = "inference",
    chunk_id: str | None = None,
    evidence_uris: list[str] | None = None,
    valid_from: str | None = None,
    reasoning_summary: str | None = "Parent reasoning.",
    confidence_score: float | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions ("
        "  entity_id, claim, confidence, evidence, derivation_type, observed_at,"
        "  chunk_id, evidence_uris, valid_from, reasoning_summary, confidence_score"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            _TEST_ENTITY,
            claim,
            "believed",
            "seed",
            derivation_type,
            "2026-06-01T12:00:00Z",
            chunk_id,
            __import__("json").dumps(evidence_uris) if evidence_uris else None,
            valid_from,
            reasoning_summary,
            confidence_score,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _supersede(
    conn: sqlite3.Connection,
    old_id: int,
    *,
    mode: str | None = None,
    **overrides: object,
) -> dict[str, object]:
    body = {**_BASE_SUPERSEDE, "old_assertion_id": old_id, **overrides}
    if mode is not None:
        __import__("os").environ["SUPERSEDE_VALIDATION_MODE"] = mode
    return _supersede_assertion_impl(body)


def _assert_quality_422(exc: HTTPException) -> None:
    assert exc.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert isinstance(exc.detail, dict)
    assert exc.detail["error"] == "assertion_quality_rejected"
    assert "quality_score" in exc.detail
    assert exc.detail["diagnostics"]
    assert exc.detail["valid_derivation_types"] == DERIVATION_TYPE_TAXONOMY


def _would_reject_warnings(
    warnings: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    return [w for w in (warnings or []) if w.get("category") == "would_reject"]


# ---------------------------------------------------------------------------
# AC1 — R3 quotation without chunk_id
# ---------------------------------------------------------------------------


def test_ac1_quotation_without_chunk_hard_422(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    old_id = _insert_parent(
        supersede_env,
        derivation_type="inference",
        evidence_uris=["documents/regulatory/rule.pdf"],
    )
    with pytest.raises(HTTPException) as exc:
        _supersede(
            supersede_env,
            old_id,
            derivation_type="quotation",
            evidence_uris=["documents/regulatory/rule.pdf"],
            chunk_id=None,
        )
    _assert_quality_422(exc.value)
    assert any(d["field"] == "chunk_id" for d in exc.value.detail["diagnostics"])


# ---------------------------------------------------------------------------
# AC2 — R4 dated claim without valid_from
# ---------------------------------------------------------------------------


def test_ac2_dated_claim_without_valid_from_hard_422(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    old_id = _insert_parent(supersede_env, valid_from=None)
    with pytest.raises(HTTPException) as exc:
        _supersede(
            supersede_env,
            old_id,
            claim="Event occurred on 2026-03-15 per the report.",
            valid_from=None,
        )
    _assert_quality_422(exc.value)
    assert any(d["field"] == "valid_from" for d in exc.value.detail["diagnostics"])


# ---------------------------------------------------------------------------
# AC3 — R2 thread_compression constraints
# ---------------------------------------------------------------------------


def test_ac3a_thread_compression_missing_evidence_uris_hard_422(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    old_id = _insert_parent(
        supersede_env,
        derivation_type="thread_compression",
        evidence_uris=["cortex://notes/system/thread.md"],
    )
    with pytest.raises(HTTPException) as exc:
        _supersede(
            supersede_env,
            old_id,
            derivation_type="thread_compression",
            evidence_uris=None,
        )
    _assert_quality_422(exc.value)
    assert any(d["field"] == "evidence_uris" for d in exc.value.detail["diagnostics"])


def test_ac3b_thread_compression_with_chunk_id_hard_422(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    old_id = _insert_parent(
        supersede_env,
        derivation_type="thread_compression",
        evidence_uris=["cortex://notes/system/thread.md"],
    )
    with pytest.raises(HTTPException) as exc:
        _supersede(
            supersede_env,
            old_id,
            derivation_type="thread_compression",
            evidence_uris=["cortex://notes/system/thread.md"],
            chunk_id="abc-0",
        )
    _assert_quality_422(exc.value)
    assert any(d["field"] == "chunk_id" for d in exc.value.detail["diagnostics"])


# ---------------------------------------------------------------------------
# AC4 — valid inherited supersede (claim rewrite only)
# ---------------------------------------------------------------------------


def test_ac4_valid_claim_rewrite_only_succeeds(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    old_id = _insert_parent(
        supersede_env,
        derivation_type="inference",
        reasoning_summary="Structured reasoning.",
        evidence_uris=["cortex://notes/system/thread.md"],
    )
    result = _supersede(
        supersede_env,
        old_id,
        claim="Rewritten index claim without quality violations.",
    )
    assert result["new"]["claim"] == "Rewritten index claim without quality violations."


# ---------------------------------------------------------------------------
# AC5 — no staging; quality_score persisted
# ---------------------------------------------------------------------------


def test_ac5_low_quality_not_staged_quality_score_persisted(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "shadow")
    old_id = _insert_parent(
        supersede_env,
        reasoning_summary="Parent reasoning.",
    )
    result = _supersede(
        supersede_env,
        old_id,
        reasoning_summary=None,
        confidence_score=0.1,
    )
    new_id = result["new"]["id"]
    row = supersede_env.execute(
        "SELECT review_status, quality_score FROM assertions WHERE id = ?",
        (new_id,),
    ).fetchone()
    assert row["review_status"] != "staged"
    assert row["quality_score"] is not None


# ---------------------------------------------------------------------------
# AC6 — observation relaxation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["shadow", "hard_422"])
def test_ac6_observation_type_dated_claim_without_valid_from_succeeds(
    supersede_env: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", mode)
    old_id = _insert_parent(
        supersede_env,
        derivation_type="agent_observation",
        valid_from=None,
    )
    result = _supersede(
        supersede_env,
        old_id,
        derivation_type="agent_observation",
        claim="Observed on 2026-04-01 that the service restarted.",
        valid_from=None,
    )
    assert result["new"]["derivation_type"] == "agent_observation"


# ---------------------------------------------------------------------------
# AC7 — force does not bypass validation
# ---------------------------------------------------------------------------


def test_ac7_force_true_still_hard_rejects(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    old_id = _insert_parent(supersede_env)
    with pytest.raises(HTTPException) as exc:
        _supersede(
            supersede_env,
            old_id,
            derivation_type="quotation",
            evidence_uris=["documents/regulatory/rule.pdf"],
            chunk_id=None,
            force=True,
        )
    _assert_quality_422(exc.value)


# ---------------------------------------------------------------------------
# AC9 — pre-mutation guarantee on hard reject
# ---------------------------------------------------------------------------


def test_ac9_hard_reject_no_row_no_superseded_by_no_recompute(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    old_id = _insert_parent(supersede_env)
    pre_count = supersede_env.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    recompute = MagicMock()
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.recompute_entity_substantiation_status",
        recompute,
    )
    with pytest.raises(HTTPException):
        _supersede(
            supersede_env,
            old_id,
            derivation_type="quotation",
            evidence_uris=["documents/regulatory/rule.pdf"],
            chunk_id=None,
        )
    post_count = supersede_env.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert post_count == pre_count
    row = supersede_env.execute(
        "SELECT superseded_by FROM assertions WHERE id = ?", (old_id,)
    ).fetchone()
    assert row["superseded_by"] is None
    recompute.assert_not_called()


# ---------------------------------------------------------------------------
# AC10 — shadow vs hard_422 mode gate
# ---------------------------------------------------------------------------


def test_ac10_shadow_writes_with_warning_hard_422_rejects(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_id = _insert_parent(supersede_env)
    body = {
        **_BASE_SUPERSEDE,
        "old_assertion_id": old_id,
        "derivation_type": "quotation",
        "evidence_uris": ["documents/regulatory/rule.pdf"],
        "chunk_id": None,
    }

    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "shadow")
    shadow_result = _supersede_assertion_impl(body)
    assert _would_reject_warnings(shadow_result.get("validation_warnings"))

    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    with pytest.raises(HTTPException) as exc:
        _supersede_assertion_impl(body)
    _assert_quality_422(exc.value)


# ---------------------------------------------------------------------------
# AC11 — masked R4 via inherited valid_from
# ---------------------------------------------------------------------------


def test_ac11_inherited_valid_from_masks_r4(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    old_id = _insert_parent(supersede_env, valid_from="2026-01-01")
    result = _supersede(
        supersede_env,
        old_id,
        claim="Updated on 2026-03-15 with inherited valid_from.",
    )
    assert result["new"]["valid_from"] == "2026-01-01"


def test_ac11_cleared_valid_from_triggers_r4(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    old_id = _insert_parent(supersede_env, valid_from="2026-01-01")
    with pytest.raises(HTTPException) as exc:
        _supersede(
            supersede_env,
            old_id,
            claim="Updated on 2026-03-15 after clearing valid_from.",
            valid_from=None,
        )
    _assert_quality_422(exc.value)


# ---------------------------------------------------------------------------
# AC12 — inherited-field type changes
# ---------------------------------------------------------------------------


def test_ac12_override_to_quotation_without_chunk_hard_422(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    old_id = _insert_parent(
        supersede_env,
        derivation_type="inference",
        chunk_id=None,
        evidence_uris=["documents/regulatory/rule.pdf"],
    )
    with pytest.raises(HTTPException) as exc:
        _supersede(
            supersede_env,
            old_id,
            derivation_type="quotation",
            evidence_uris=["documents/regulatory/rule.pdf"],
        )
    _assert_quality_422(exc.value)


def test_ac12_override_to_thread_compression_with_inherited_chunk_hard_422(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    old_id = _insert_parent(
        supersede_env,
        derivation_type="compression",
        chunk_id="abc-0",
        evidence_uris=["documents/regulatory/rule.pdf"],
    )
    with pytest.raises(HTTPException) as exc:
        _supersede(
            supersede_env,
            old_id,
            derivation_type="thread_compression",
            evidence_uris=["cortex://notes/system/thread.md"],
        )
    _assert_quality_422(exc.value)


# ---------------------------------------------------------------------------
# AC13 — CAS + force still validates
# ---------------------------------------------------------------------------


def test_ac13_cas_failure_leaves_no_orphan_replacement(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    successor = _insert_parent(supersede_env)
    target = _insert_parent(supersede_env)
    supersede_env.execute(
        "UPDATE assertions SET superseded_by = ? WHERE id = ?",
        (successor, target),
    )
    supersede_env.commit()
    pre_count = supersede_env.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    with pytest.raises(HTTPException) as exc:
        _supersede(supersede_env, target, claim="Valid replacement claim.")
    assert exc.value.status_code == 409
    post_count = supersede_env.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert post_count == pre_count


def test_ac13_force_true_still_validates_effective_values(
    supersede_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    successor = _insert_parent(supersede_env)
    target = _insert_parent(supersede_env)
    supersede_env.execute(
        "UPDATE assertions SET superseded_by = ? WHERE id = ?",
        (successor, target),
    )
    supersede_env.commit()
    with pytest.raises(HTTPException) as exc:
        _supersede(
            supersede_env,
            target,
            derivation_type="quotation",
            evidence_uris=["documents/regulatory/rule.pdf"],
            chunk_id=None,
            force=True,
        )
    _assert_quality_422(exc.value)


# ---------------------------------------------------------------------------
# AC14 — synthetic AssertionCreate construction
# ---------------------------------------------------------------------------


def test_ac14_synthetic_preserves_observation_relaxation() -> None:
    synthetic = AssertionCreate.model_validate(
        {
            "entity_id": _TEST_ENTITY,
            "claim": "Observed on 2026-05-01 that the daemon restarted.",
            "confidence": "believed",
            "evidence": "unit test",
            "derivation_type": "direct_observation",
            "observed_at": "2026-06-23T12:00:00Z",
            "valid_from": None,
        }
    )
    result = validate_assertion(synthetic)
    assert not result.rejected


# ---------------------------------------------------------------------------
# AC8 collateral — create path unchanged (spot check via existing create)
# ---------------------------------------------------------------------------


def test_ac8_create_still_rejects_quotation_without_chunk(
    supersede_env: sqlite3.Connection,
) -> None:
    with pytest.raises(HTTPException) as exc:
        _create_assertion_impl(
            {
                "entity_id": _TEST_ENTITY,
                "claim": '"Verbatim quote from source document here."',
                "confidence": "believed",
                "evidence": "unit test",
                "derivation_type": "quotation",
                "evidence_uris": ["documents/regulatory/rule.pdf"],
                "observed_at": "2026-06-23T12:00:00Z",
            }
        )
    _assert_quality_422(exc.value)
