"""Unit + parity tests for provenance detectors B, C-attrs, D2 and D1 wiring."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from cortex_store.assertion_quality import (
    check_chunk_locality,
    check_derived_extract_primary,
    check_provenance_staging_attrs,
)
from cortex_store.dispatch_ops._detectors.provenance_staging import (
    detect_provenance_cites_staging,
)
from cortex_store.models._shared import (
    STAGING_PREFIXES,
    first_segment_after_internal_normalize,
    reject_cortex_dropbox_source_uri,
    uri_first_segment_is_staging,
)
from cortex_store.routes.assertions import (
    _create_assertion_impl,
    _supersede_assertion_impl,
)


def _prov_warnings(warnings: list[dict[str, str]] | None) -> list[dict[str, str]]:
    return [w for w in (warnings or []) if w.get("category") == "provenance"]


# ---------------------------------------------------------------------------
# F1 — shared pydantic matcher (HARD STOP baseline + new cases)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        "dropbox/staged.pdf",
        "files://dropbox/staged.pdf",
        "cortex://dropbox/staged.pdf",
        "ws://universal-llm-gateway/dropbox/staged.pdf",
        "workspaces://universal-llm-gateway/dropbox/staged.pdf",
    ],
)
def test_staging_uri_hard_reject(uri: str) -> None:
    with pytest.raises(ValueError, match="dropbox"):
        reject_cortex_dropbox_source_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "https://www.dropbox.com/s/abc/file.pdf",
        "https://dropbox.com/x",
        "files://documents/vendor/dropbox-api-guide.pdf",
        "documents/notes/dropbox-migration.md",
        "notes/system/a/dropbox/b/file.pdf",
    ],
)
def test_staging_uri_allowed(uri: str) -> None:
    assert reject_cortex_dropbox_source_uri(uri) == uri


def test_first_segment_matcher_not_any_position() -> None:
    assert first_segment_after_internal_normalize("a/dropbox/b") == "a"
    assert not uri_first_segment_is_staging("a/dropbox/b")


def test_staging_prefixes_single_source() -> None:
    assert STAGING_PREFIXES == frozenset({"dropbox"})


# ---------------------------------------------------------------------------
# B — derived extract cited before primary
# ---------------------------------------------------------------------------


def test_b_extract_before_primary_warns() -> None:
    uris = [
        "documents/regulatory/pge-rule-17_1-backbilling.md",
        "documents/regulatory/pge-rule-17_1-backbilling.pdf",
    ]
    w = check_derived_extract_primary(uris)
    assert w
    assert w[0]["category"] == "provenance"
    assert "possible ordering inversion" in w[0]["message"]


def test_b_primary_first_silent() -> None:
    uris = [
        "documents/regulatory/pge-rule-17_1-backbilling.pdf",
        "documents/regulatory/pge-rule-17_1-backbilling.md",
    ]
    assert check_derived_extract_primary(uris) == []


def test_b_extract_alone_silent() -> None:
    uris = ["documents/regulatory/pge-rule-17_1-backbilling.md"]
    assert check_derived_extract_primary(uris) == []


# ---------------------------------------------------------------------------
# C-attrs — targeted provenance attribute keys (advisory)
# ---------------------------------------------------------------------------


def test_c_attrs_moved_from_staging_warns() -> None:
    w = check_provenance_staging_attrs({"moved_from": "dropbox/old/path.pdf"})
    assert w
    assert w[0]["field"] == "moved_from"
    assert w[0]["category"] == "provenance"


def test_c_attrs_non_provenance_key_silent() -> None:
    assert check_provenance_staging_attrs({"title": "dropbox/staged.pdf"}) == []


def test_c_attrs_permanent_path_silent() -> None:
    assert check_provenance_staging_attrs({"source_uri": "documents/legal/bill.pdf"}) == []


# ---------------------------------------------------------------------------
# D2 — narrow chunk-locality
# ---------------------------------------------------------------------------


def test_d2_document_quote_without_chunk_warns() -> None:
    claim = (
        'The tariff states: "Backbilling adjustments apply when usage '
        'differences exceed the stated threshold for the billing period."'
    )
    w = check_chunk_locality(
        derivation_type="direct_observation",
        claim=claim,
        evidence_uris=["documents/regulatory/rule.pdf"],
        chunk_id=None,
    )
    assert w
    assert w[0]["field"] == "chunk_id"
    assert w[0]["category"] == "provenance"


def test_d2_non_rag_evidence_silent() -> None:
    w = check_chunk_locality(
        derivation_type="inference",
        claim='"Some quoted span long enough to qualify here."',
        evidence_uris=["cortex://notes/system/thread.md"],
        chunk_id=None,
    )
    assert w == []


def test_d2_title_case_label_suppressed() -> None:
    claim = 'Observed label "Rule Seventeen Point One Backbilling" in the UI.'
    w = check_chunk_locality(
        derivation_type="direct_observation",
        claim=claim,
        evidence_uris=["documents/regulatory/rule.pdf"],
        chunk_id=None,
    )
    assert w == []


def test_d2_blockquote_counts_as_quote() -> None:
    claim = (
        "> Backbilling adjustments apply when usage differences exceed the "
        "stated threshold for the billing period and remain unaddressed."
    )
    w = check_chunk_locality(
        derivation_type="other",
        claim=claim,
        evidence_uris=["documents/regulatory/rule.pdf"],
        chunk_id=None,
    )
    assert w


# ---------------------------------------------------------------------------
# Audit — provenance_cites_staging
# ---------------------------------------------------------------------------


def test_audit_flags_staging_in_durable_doc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files_root = tmp_path / "files"
    doc = files_root / "notes" / "system" / "provenance-table.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("| source | dropbox/staged/report.pdf |\n", encoding="utf-8")

    monkeypatch.setattr(
        "cortex_store.dispatch_ops._detectors.provenance_staging._FILES_ROOT",
        files_root,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._detectors.provenance_staging._SCAN_ROOTS",
        (
            files_root / "tasks" / "specs",
            files_root / "notes",
            files_root / "documents",
        ),
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE entities (id TEXT PRIMARY KEY, attributes TEXT)")
    conn.commit()
    findings = detect_provenance_cites_staging(conn)
    assert any(f["kind"] == "provenance_cites_staging" for f in findings)
    assert any("notes/system/provenance-table.md:1" in f["subject"] for f in findings)


# ---------------------------------------------------------------------------
# D1 parity — symmetric advisory surface (create vs supersede)
# ---------------------------------------------------------------------------


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
def parity_env(
    monkeypatch: pytest.MonkeyPatch, migrated_conn: sqlite3.Connection
) -> sqlite3.Connection:
    conn = migrated_conn
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'test', ?)",
        ("test:entity", "test-entity"),
    )
    conn.commit()
    wrapper = _NoCloseConn(conn)

    for mod in ("_create", "_supersede"):
        prefix = f"cortex_store.routes.assertions.{mod}"
        monkeypatch.setattr(f"{prefix}.cortex_conn", lambda w=wrapper: w)
        monkeypatch.setattr(f"{prefix}.enrich_background", lambda *a, **kw: None)
        monkeypatch.setattr(f"{prefix}.reindex_assertion_fts", lambda *a, **kw: None)
        monkeypatch.setattr(f"{prefix}._embed_assertion_background", lambda *a, **kw: None)
        monkeypatch.setattr(f"{prefix}.threading.Thread", _NoOpThread)

    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.guard_assertion_write",
        lambda *a, **kw: type("G", (), {"allowed": True, "block_detail": "", "review_status": None, "contradiction_warnings": []})(),
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.check_contradictions",
        lambda *a, **kw: type("C", (), {"flagged": False})(),
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.check_near_duplicate",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.record_near_duplicate",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._create.dispatch_predicate_extract_background",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.enrich_old_assertion_events",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.analyze_assertion_impact",
        lambda *a, **kw: type("I", (), {"likely_supersedes": [], "touched_assertions": []})(),
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.compute_entrenchment",
        lambda **kw: 0.5,
    )

    class _FakeVS:
        @staticmethod
        def is_initialized() -> bool:
            return False

        @staticmethod
        def delete_assertion_embedding(_id: int) -> None:
            return None

    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.vector_store", _FakeVS
    )
    return conn


def _base_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "entity_id": "test:entity",
        "claim": "Index claim for provenance parity.",
        "confidence": "believed",
        "evidence": "unit test",
        "derivation_type": "inference",
        "observed_at": "2026-06-23T12:00:00Z",
        "reasoning_summary": "Structured reasoning for parity fixture.",
    }
    body.update(overrides)
    return body


def test_d1_parity_b_warnings_match_create_and_supersede(
    parity_env: sqlite3.Connection,
) -> None:
    body = _base_body(
        evidence_uris=[
            "documents/regulatory/pge-rule-17_1-backbilling.md",
            "documents/regulatory/pge-rule-17_1-backbilling.pdf",
        ]
    )
    create_result = _create_assertion_impl(body)
    create_prov = _prov_warnings(create_result.get("validation_warnings"))

    old_id = parity_env.execute(
        "SELECT id FROM assertions ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    supersede_body = {
        **body,
        "old_assertion_id": old_id,
        "session_id": "parity-session",
        "agent": "test",
    }
    supersede_result = _supersede_assertion_impl(supersede_body)
    supersede_prov = _prov_warnings(supersede_result.get("validation_warnings"))

    assert create_prov and supersede_prov
    assert create_prov[0]["message"] == supersede_prov[0]["message"]


def test_d1_parity_d2_warnings_match_create_and_supersede(
    parity_env: sqlite3.Connection,
) -> None:
    claim = (
        'Footer reads: "Backbilling adjustments apply when usage differences '
        'exceed the stated threshold for the billing period."'
    )
    body = _base_body(
        derivation_type="direct_observation",
        claim=claim,
        evidence_uris=["documents/regulatory/rule.pdf"],
        chunk_id=None,
    )
    create_result = _create_assertion_impl(body)
    create_prov = _prov_warnings(create_result.get("validation_warnings"))

    old_id = parity_env.execute(
        "SELECT id FROM assertions ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    supersede_result = _supersede_assertion_impl(
        {
            **body,
            "old_assertion_id": old_id,
            "session_id": "parity-session",
            "agent": "test",
        }
    )
    supersede_prov = _prov_warnings(supersede_result.get("validation_warnings"))
    assert create_prov and supersede_prov
    assert create_prov[0]["field"] == supersede_prov[0]["field"] == "chunk_id"


# ---------------------------------------------------------------------------
# D1 — documented validate_assertion asymmetry (xfail)
# ---------------------------------------------------------------------------


def test_d1_hard_reject_quotation_without_chunk_parity_in_hard_mode(
    parity_env: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPERSEDE_VALIDATION_MODE", "hard_422")
    body = _base_body(
        derivation_type="quotation",
        claim='"Verbatim quote from source document here."',
        evidence_uris=["documents/regulatory/rule.pdf"],
        chunk_id=None,
    )

    create_rejected = False
    try:
        _create_assertion_impl(body)
    except HTTPException as exc:
        create_rejected = exc.status_code == 422

    old_id = parity_env.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, derivation_type, observed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "test:entity",
            "Seed.",
            "believed",
            "seed",
            "inference",
            "2026-06-23T12:00:00Z",
        ),
    ).lastrowid
    parity_env.commit()

    supersede_rejected = False
    try:
        _supersede_assertion_impl(
            {
                **body,
                "old_assertion_id": old_id,
                "session_id": "parity-session",
                "agent": "test",
            }
        )
    except HTTPException as exc:
        supersede_rejected = exc.status_code == 422

    assert create_rejected
    assert create_rejected == supersede_rejected


def test_d1_supersede_never_stages_on_missing_reasoning_summary(
    parity_env: sqlite3.Connection,
) -> None:
    body = _base_body(reasoning_summary=None)
    del body["reasoning_summary"]

    create_result = _create_assertion_impl(body)
    assert create_result["item"]["review_status"] == "staged"

    old_id = parity_env.execute(
        "SELECT id FROM assertions ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    supersede_result = _supersede_assertion_impl(
        {
            **body,
            "old_assertion_id": old_id,
            "session_id": "parity-session",
            "agent": "test",
        }
    )
    new_id = supersede_result["new"]["id"]
    row = parity_env.execute(
        "SELECT review_status, quality_score FROM assertions WHERE id = ?",
        (new_id,),
    ).fetchone()
    assert row["review_status"] != "staged"
    assert row["quality_score"] is not None


def test_c_write_cortex_dropbox_payload_validation() -> None:
    with pytest.raises(ValueError, match="dropbox"):
        reject_cortex_dropbox_source_uri("cortex://dropbox/x")
