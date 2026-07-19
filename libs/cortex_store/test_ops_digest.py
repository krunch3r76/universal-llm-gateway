"""Hermetic tests for digest op orchestration."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from cortex_store.conftest import bind_cortex_db
from cortex_store.digest_ledger import compute_entry_content_sha256
from cortex_store.digest_ledger import write as ledger_write
from cortex_store.dispatch_ops import _OP_SPECS
from cortex_store.dispatch_ops.ops_digest import _op_digest
from cortex_store.journal_digest_extract import _max_tokens, _request_timeout
from cortex_store.journal_digest_verify import (
    _max_tokens as verify_max_tokens,
)
from cortex_store.journal_digest_verify import (
    _request_timeout as verify_request_timeout,
)

_JOURNAL_ENTITY = "document:journal-2026-07-13"
_ENTRY_ANCHOR = "2026-07-13#wells-fargo-ploc"
_ENTRY_TEXT = (
    "Operator called Wells Fargo on 2026-07-13. A rep named Michael (?) stated "
    "the PLOC payment was 5 days overdue."
)
_JOURNAL_URI = "cortex://notes/journal/2026-07-13.md"

_BASE_CLAIM = {
    "claim": (
        "WF rep Michael (?) called 2026-07-13 stating PLOC payment 5 days overdue"
    ),
    "p_class": "P2",
    "canonicality": "assert",
    "attach_hint": "finance:wf-ploc",
    "flags": ["name_uncertain"],
    "evidence_anchor": "wells-fargo-ploc",
}

_FLAGGED_CLAIM = {
    **_BASE_CLAIM,
    "verify_verdict": "flag",
    "verify_note": "phrasing_ambiguous",
}

_VERIFIED_BATCH = {
    "entry_anchor": _ENTRY_ANCHOR,
    "journal_uri": _JOURNAL_URI,
    "claims": [_FLAGGED_CLAIM],
    "verify_verdicts": {"0": {"verdict": "flag", "note": "phrasing_ambiguous"}},
}

_DEDUP_SKIP_CLAIM = {
    **_BASE_CLAIM,
    "verify_verdict": "pass",
    "duplicate_of": 9001,
    "dedup_candidate_fingerprint": "deadbeef",
    "dedup_candidates": [{"id": 9001, "fingerprint": "deadbeef"}],
}

_VERIFIED_DEDUP_BATCH = {
    "entry_anchor": _ENTRY_ANCHOR,
    "journal_uri": _JOURNAL_URI,
    "claims": [_DEDUP_SKIP_CLAIM],
    "verify_verdicts": {"0": {"verdict": "pass", "note": ""}},
}


def _digest_args(**overrides: object) -> dict[str, object]:
    base = {
        "journal_entity_id": _JOURNAL_ENTITY,
        "entry_anchor": _ENTRY_ANCHOR,
        "entry_text": _ENTRY_TEXT,
        "journal_uri": _JOURNAL_URI,
    }
    base.update(overrides)
    return base


@pytest.mark.offline
def test_digest_registered_in_op_specs() -> None:
    assert "digest" in _OP_SPECS


@pytest.mark.offline
def test_watermark_skip_same_sha(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    sha = compute_entry_content_sha256(_ENTRY_TEXT)
    with patch("cortex_store.dispatch_ops.ops_digest.cortex_conn") as mock_conn:
        from cortex_store.db import cortex_conn

        mock_conn.side_effect = cortex_conn
        with cortex_conn() as conn:
            ledger_write(
                conn,
                journal_entity_id=_JOURNAL_ENTITY,
                entry_anchor=_ENTRY_ANCHOR,
                content_sha256=sha,
                emitted_ids=[101],
                staging_batch_id="batch-old",
            )
            conn.commit()

    result = _op_digest(**_digest_args())
    assert result["status"] == "skipped"
    assert result["reason"] == "watermark_match"


@pytest.mark.offline
def test_changed_sha_enters_revision_pass(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    with patch("cortex_store.dispatch_ops.ops_digest.cortex_conn") as mock_conn:
        from cortex_store.db import cortex_conn

        mock_conn.side_effect = cortex_conn
        with cortex_conn() as conn:
            ledger_write(
                conn,
                journal_entity_id=_JOURNAL_ENTITY,
                entry_anchor=_ENTRY_ANCHOR,
                content_sha256=compute_entry_content_sha256("older body"),
                emitted_ids=[],
            )
            conn.commit()

    revision_result = {
        "status": "revision_staged",
        "reason": "content_sha_changed",
        "ledger_id": 99,
        "staging_batch_id": "batch-rev-1",
        "emitted_ids": [1, 2],
    }
    with (
        patch(
            "cortex_store.dispatch_ops.ops_digest.run_revision_pass",
            return_value=revision_result,
        ) as mock_revision,
        patch("cortex_store.dispatch_ops.ops_digest.extract_claims") as mock_extract,
    ):
        result = _op_digest(**_digest_args())
        mock_extract.assert_not_called()
        mock_revision.assert_called_once()

    assert result["status"] == "revision_staged"
    assert result["reason"] == "content_sha_changed"


@pytest.mark.offline
def test_verify_none_returns_error_no_stage(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    with (
        patch(
            "cortex_store.dispatch_ops.ops_digest.extract_claims",
            return_value=_VERIFIED_BATCH,
        ),
        patch(
            "cortex_store.dispatch_ops.ops_digest.verify_claim_batch",
            return_value=None,
        ),
    ):
        result = _op_digest(**_digest_args())

    assert result["error"] == "verify_failed"
    with patch("cortex_store.dispatch_ops.ops_digest.cortex_conn") as _:
        from cortex_store.db import cortex_conn

        with cortex_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM extraction_staging"
            ).fetchone()["n"]
    assert count == 0


@pytest.mark.offline
def test_happy_path_stages_rows_and_writes_ledger(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    with (
        patch(
            "cortex_store.dispatch_ops.ops_digest.extract_claims",
            return_value=_VERIFIED_BATCH,
        ),
        patch(
            "cortex_store.dispatch_ops.ops_digest.verify_claim_batch",
            return_value=_VERIFIED_BATCH,
        ),
    ):
        result = _op_digest(**_digest_args())

    assert result["status"] == "staged"
    assert result["staged_counts"]["assertion"] == 1
    assert result["staged_counts"]["entity"] == 1
    assert result["emitted_ids"]
    assert result["flagged_claim_indices"] == [0]

    from cortex_store.db import cortex_conn

    with cortex_conn() as conn:
        ledger = conn.execute(
            "SELECT * FROM digest_ledger WHERE id = ?",
            (result["ledger_id"],),
        ).fetchone()
        assert ledger is not None
        assert ledger["staging_batch_id"] == result["staging_batch_id"]


@pytest.mark.offline
def test_flagged_claim_still_present_in_staged_batch(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    with (
        patch(
            "cortex_store.dispatch_ops.ops_digest.extract_claims",
            return_value=_VERIFIED_BATCH,
        ),
        patch(
            "cortex_store.dispatch_ops.ops_digest.verify_claim_batch",
            return_value=_VERIFIED_BATCH,
        ),
    ):
        result = _op_digest(**_digest_args())

    from cortex_store.db import cortex_conn, json_decode

    with cortex_conn() as conn:
        placeholders = ",".join("?" * len(result["emitted_ids"]))
        rows = conn.execute(
            "SELECT id, proposal_type, proposal_json FROM extraction_staging "
            f"WHERE id IN ({placeholders})",
            tuple(result["emitted_ids"]),
        ).fetchall()
        assertion_rows = [
            json_decode(row["proposal_json"])
            for row in rows
            if row["proposal_type"] == "assertion"
        ]

    assert len(assertion_rows) == 1
    assert assertion_rows[0]["claim"] == _FLAGGED_CLAIM["claim"]


@pytest.mark.offline
def test_env_defaults_for_digest_token_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CORTEX_DIGEST_MAX_TOKENS", raising=False)
    monkeypatch.delenv("CORTEX_DIGEST_TIMEOUT_S", raising=False)
    assert _max_tokens() == 16384
    assert _request_timeout() == 180.0
    assert verify_max_tokens() == 16384
    assert verify_request_timeout() == 180.0

    monkeypatch.setenv("CORTEX_DIGEST_MAX_TOKENS", "8192")
    monkeypatch.setenv("CORTEX_DIGEST_TIMEOUT_S", "90")
    assert _max_tokens() == 8192
    assert _request_timeout() == 90.0
    assert verify_max_tokens() == 8192
    assert verify_request_timeout() == 90.0
    assert os.environ["CORTEX_DIGEST_MAX_TOKENS"] == "8192"


@pytest.mark.offline
def test_semantic_duplicate_of_emits_skipped_dedup(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from datetime import UTC, datetime

    from cortex_store.claim_hash import compute_claim_hash
    from cortex_store.db import cortex_conn
    from cortex_store.digest_dedup import compute_dedup_candidate_fingerprint

    claim_text = _BASE_CLAIM["claim"]
    journal_uri = _JOURNAL_URI
    with cortex_conn() as conn:
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO entities (id, type, name, lifecycle, created_at, updated_at) "
            "VALUES (?, ?, ?, 'active', ?, ?)",
            ("finance:wf-ploc", "finance", "Wells Fargo PLOC", now, now),
        )
        claim_hash = compute_claim_hash("finance:wf-ploc", claim_text)
        cur = conn.execute(
            "INSERT INTO assertions "
            "(entity_id, claim, claim_hash, confidence, derivation_type, evidence_uris, "
            "created_at) VALUES (?, ?, ?, 'believed', 'user_statement', ?, ?)",
            (
                "finance:wf-ploc",
                claim_text,
                claim_hash,
                __import__("json").dumps([journal_uri]),
                now,
            ),
        )
        existing_id = int(cur.lastrowid or 0)
        conn.execute(
            "INSERT INTO assertions_fts (assertion_id, entity_id, indexed_text) "
            "VALUES (?, ?, ?)",
            (existing_id, "finance:wf-ploc", claim_text),
        )
        conn.commit()
        fingerprint = compute_dedup_candidate_fingerprint(
            assertion_id=existing_id,
            entity_id="finance:wf-ploc",
            claim=claim_text,
            derivation_type="user_statement",
            evidence_uris=[journal_uri],
            valid_from=None,
            valid_until=None,
        )

    dedup_claim = {
        **_BASE_CLAIM,
        "verify_verdict": "pass",
        "duplicate_of": existing_id,
        "dedup_candidate_fingerprint": fingerprint,
        "dedup_candidates": [
            {
                "id": existing_id,
                "fingerprint": fingerprint,
                "entity_id": "finance:wf-ploc",
                "claim": claim_text,
            }
        ],
    }
    verified_batch = {
        "entry_anchor": _ENTRY_ANCHOR,
        "journal_uri": journal_uri,
        "claims": [dedup_claim],
        "verify_verdicts": {"0": {"verdict": "pass", "note": ""}},
    }

    with (
        patch(
            "cortex_store.dispatch_ops.ops_digest.extract_claims",
            return_value=verified_batch,
        ),
        patch(
            "cortex_store.dispatch_ops.ops_digest.verify_claim_batch",
            return_value=verified_batch,
        ),
    ):
        result = _op_digest(**_digest_args())

    assert result["status"] == "staged"
    assert result["skipped_dedups"] == [f"assertion:{existing_id}"]
    assert result["staged_counts"]["assertion"] == 0


_WHOLE_ENTRY_TEXT = """\
# Health
Ingested 25 mg of AnazaoHealth enclomiphene 25 mg sublingually in the morning.

# Health symptoms
Nodding off repeatedly in the day, entering a dream state.

# Wells Fargo calls for payment on my PLOC
A Michael (?) from Wells Fargo called to inform me my payment is 5 days overdue.
"""

_SECTION_BATCH = {
    "entry_anchor": "2026-07-13#health",
    "journal_uri": _JOURNAL_URI,
    "claims": [_BASE_CLAIM],
    "verify_verdicts": {"0": {"verdict": "pass", "note": ""}},
}


@pytest.mark.offline
def test_auto_segment_digests_whole_entry_without_caller_split(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    with (
        patch(
            "cortex_store.dispatch_ops.ops_digest.extract_claims",
            return_value=_SECTION_BATCH,
        ),
        patch(
            "cortex_store.dispatch_ops.ops_digest.verify_claim_batch",
            return_value=_SECTION_BATCH,
        ),
    ):
        result = _op_digest(
            journal_entity_id=_JOURNAL_ENTITY,
            entry_text=_WHOLE_ENTRY_TEXT,
            journal_uri=_JOURNAL_URI,
            auto_segment=True,
            entry_date="2026-07-13",
        )

    assert result["status"] == "segmented"
    assert len(result["sections"]) == 3
    anchors = [row["entry_anchor"] for row in result["sections"]]
    assert anchors == [
        "2026-07-13#health",
        "2026-07-13#health-symptoms",
        "2026-07-13#wells-fargo-calls-for-payment-on-my-ploc",
    ]
    assert all(row["status"] == "staged" for row in result["sections"])
    assert result["summary"]["staged"] == 3


@pytest.mark.offline
def test_auto_segment_requires_entry_date() -> None:
    result = _op_digest(
        journal_entity_id=_JOURNAL_ENTITY,
        entry_text=_WHOLE_ENTRY_TEXT,
        auto_segment=True,
    )
    assert result["code"] == "missing_entry_date"
