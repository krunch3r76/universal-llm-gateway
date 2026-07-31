"""Hermetic tests for digest revision pass orchestration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cortex_store.conftest import bind_cortex_db
from cortex_store.digest_ledger import compute_entry_content_sha256, write as ledger_write
from cortex_store.digest_revision_pass import run_revision_pass

_JOURNAL = "document:journal-2026-07-17"
_ANCHOR = "2026-07-17#overnight-rideshare-micro-sleeps"
_URI = "cortex://notes/journal/kaywan/2026-07-17.md"
_PRIOR_TEXT = "Micro-sleeps with circadian surprise."
_REVISED_TEXT = (
    "Micro-sleeps with circadian surprise.\n\n"
    '*Revised 2026-07-19 — operator:* "Actually it was not circadian — up since 4am."'
)

_REVISION_BATCH = {
    "entry_anchor": _ANCHOR,
    "journal_uri": _URI,
    "prior_assertions": [
        {
            "id": 42,
            "entity_id": _JOURNAL,
            "claim": "Cause frame: circadian",
            "derivation_type": "inference",
            "confidence": "suspected",
            "evidence_uris": [_URI],
        }
    ],
    "decisions": [
        {
            "prior_id": 42,
            "decision": "revise",
            "verbatim_evidence": "Actually it was not circadian — up since 4am.",
            "successor": {
                "claim": "Cause: awake since 04:00, not circadian",
                "p_class": "P3",
            },
        }
    ],
    "adds": [],
    "flags": [],
}


@pytest.mark.offline
def test_revision_pass_stages_supersede_and_ledger(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from cortex_store.db import cortex_conn

    with cortex_conn() as conn:
        now = "2026-07-19T00:00:00Z"
        conn.execute(
            "INSERT INTO entities (id, type, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_JOURNAL, "document", "journal", now, now),
        )
        ledger_write(
            conn,
            journal_entity_id=_JOURNAL,
            entry_anchor=_ANCHOR,
            content_sha256=compute_entry_content_sha256(_PRIOR_TEXT),
            emitted_ids=[42],
            status="committed",
        )
        conn.execute(
            "INSERT INTO assertions "
            "(id, entity_id, claim, confidence, derivation_type, evidence_uris, "
            "reasoning_summary, created_at) "
            "VALUES (42, ?, ?, 'suspected', 'inference', ?, ?, datetime('now'))",
            (
                _JOURNAL,
                "Cause frame: circadian",
                '["cortex://notes/journal/kaywan/2026-07-17.md"]',
                f"digest:{_ANCHOR}#1",
            ),
        )
        conn.commit()

    with patch(
        "cortex_store.digest_revision_pass.extract_revision_decisions",
        return_value=_REVISION_BATCH,
    ):
        result = run_revision_pass(
            journal_entity_id=_JOURNAL,
            entry_anchor=_ANCHOR,
            entry_text=_REVISED_TEXT,
            journal_uri=_URI,
        )

    assert result["status"] == "revision_staged"
    assert result["superseded_ids"] == [42]
    assert result["emitted_ids"]

    with cortex_conn() as conn:
        ledger = conn.execute(
            "SELECT status, revision_of, superseded_ids FROM digest_ledger WHERE id = ?",
            (result["ledger_id"],),
        ).fetchone()
        assert ledger["status"] == "staged"
        assert ledger["revision_of"] == 1

        staging = conn.execute(
            "SELECT proposal_action, target_id FROM extraction_staging WHERE id = ?",
            (result["emitted_ids"][0],),
        ).fetchone()
        assert staging["proposal_action"] == "revise"
        assert staging["target_id"] == "42"
