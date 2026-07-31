"""Hermetic tests for digest_jobs state machine and dedupe."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store.digest_jobs import (
    enqueue_extract,
    job_status,
    retry_job,
    tick_jobs,
)
from cortex_store.digest_ledger import compute_entry_content_sha256
from cortex_store.digest_ledger import write as ledger_write
from cortex_store.journal_digest_extract_cdp import PROMPT_REV_SOFT_V2

_MIG068 = Path(__file__).parent / "migrations" / "068_digest_ledger.py"
_MIG071 = Path(__file__).parent / "migrations" / "071_digest_ledger_revision.py"
_MIG072 = Path(__file__).parent / "migrations" / "072_digest_jobs.py"


def _load_migration(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


migration_068 = _load_migration(_MIG068, "migration_068")
migration_071 = _load_migration(_MIG071, "migration_071")
migration_072 = _load_migration(_MIG072, "migration_072")

_JOURNAL = "document:journal-test"
_ANCHOR = "2026-07-17#overnight-rideshare-micro-sleeps"
_TEXT = "Operator noted micro-sleeps while driving rideshare overnight."


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migration_068.migrate(c)
    migration_071.migrate(c)
    migration_072.migrate(c)
    return c


@pytest.mark.offline
def test_enqueue_inserts_enqueued_row(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CDP_PROJECT_UUID", "proj-test")
    with patch("cortex_store.digest_jobs.cortex_conn") as mock_conn:
        mock_conn.side_effect = lambda: conn
        result = enqueue_extract(
            journal_entity_id=_JOURNAL,
            entry_anchor=_ANCHOR,
            entry_text=_TEXT,
            journal_uri="cortex://notes/journal/2026-07-17.md",
        )

    assert result["status"] == "enqueued"
    row = conn.execute(
        "SELECT state, kind, prompt_rev, model FROM digest_jobs WHERE job_id = ?",
        (result["job_id"],),
    ).fetchone()
    assert row["state"] == "ENQUEUED"
    assert row["kind"] == "extract"
    assert row["prompt_rev"] == PROMPT_REV_SOFT_V2


@pytest.mark.offline
def test_dedupe_returns_open_job_id(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CDP_PROJECT_UUID", "proj-test")
    with patch("cortex_store.digest_jobs.cortex_conn") as mock_conn:
        mock_conn.side_effect = lambda: conn
        first = enqueue_extract(
            journal_entity_id=_JOURNAL,
            entry_anchor=_ANCHOR,
            entry_text=_TEXT,
        )
        second = enqueue_extract(
            journal_entity_id=_JOURNAL,
            entry_anchor=_ANCHOR,
            entry_text=_TEXT,
        )

    assert first["status"] == "enqueued"
    assert second["status"] == "deduped"
    assert second["job_id"] == first["job_id"]


@pytest.mark.offline
def test_watermark_skip_no_job(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CDP_PROJECT_UUID", "proj-test")
    sha = compute_entry_content_sha256(_TEXT)
    ledger_write(
        conn,
        journal_entity_id=_JOURNAL,
        entry_anchor=_ANCHOR,
        content_sha256=sha,
        emitted_ids=[],
        staging_batch_id="batch-1",
    )
    conn.commit()

    with patch("cortex_store.digest_jobs.cortex_conn") as mock_conn:
        mock_conn.side_effect = lambda: conn
        result = enqueue_extract(
            journal_entity_id=_JOURNAL,
            entry_anchor=_ANCHOR,
            entry_text=_TEXT,
        )

    assert result["status"] == "skipped"
    assert conn.execute("SELECT COUNT(*) AS n FROM digest_jobs").fetchone()["n"] == 0


@pytest.mark.offline
def test_revision_detect_enqueues_revision_extract(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CDP_PROJECT_UUID", "proj-test")
    ledger_write(
        conn,
        journal_entity_id=_JOURNAL,
        entry_anchor=_ANCHOR,
        content_sha256=compute_entry_content_sha256("older text"),
        emitted_ids=[1],
        staging_batch_id="batch-old",
        status="staged",
    )
    conn.commit()

    with patch("cortex_store.digest_jobs.cortex_conn") as mock_conn:
        mock_conn.side_effect = lambda: conn
        result = enqueue_extract(
            journal_entity_id=_JOURNAL,
            entry_anchor=_ANCHOR,
            entry_text=_TEXT,
        )

    assert result["status"] == "enqueued"
    assert result["kind"] == "revision_extract"


@pytest.mark.offline
def test_model_mismatch_parks_model_unavailable(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CDP_PROJECT_UUID", "proj-test")
    with patch("cortex_store.digest_jobs.cortex_conn") as mock_conn:
        mock_conn.side_effect = lambda: conn
        enq = enqueue_extract(
            journal_entity_id=_JOURNAL,
            entry_anchor=_ANCHOR,
            entry_text=_TEXT,
        )

    cdp_result = {
        "execution_id": "exec-1",
        "prompt_uri": "cortex://ephemeral/digest/prompt.md",
        "prompt_sha256": "sha256:abc",
        "park_reason": "model_unavailable",
        "error": "model_unavailable",
    }

    with (
        patch("cortex_store.digest_jobs.cortex_conn") as mock_conn,
        patch(
            "cortex_store.digest_jobs.run_cdp_extract_for_job",
            return_value=cdp_result,
        ),
    ):
        mock_conn.side_effect = lambda: conn
        tick_jobs(limit=1)

    row = conn.execute(
        "SELECT state, last_error FROM digest_jobs WHERE job_id = ?",
        (enq["job_id"],),
    ).fetchone()
    assert row["state"] == "ENQUEUED"
    assert row["last_error"] == "model_unavailable"

    with (
        patch("cortex_store.digest_jobs.cortex_conn") as mock_conn,
        patch(
            "cortex_store.digest_jobs.run_cdp_extract_for_job",
            return_value=cdp_result,
        ),
    ):
        mock_conn.side_effect = lambda: conn
        tick_jobs(limit=1)

    row = conn.execute(
        "SELECT state, last_error FROM digest_jobs WHERE job_id = ?",
        (enq["job_id"],),
    ).fetchone()
    assert row["state"] == "PARKED"
    assert "model_unavailable" in str(row["last_error"])


@pytest.mark.offline
def test_retry_parked_job(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO digest_jobs (
            job_id, kind, journal_entity_id, entry_anchor, entry_text,
            content_sha256, prompt_rev, model, state, last_error
        ) VALUES ('job-1', 'extract', ?, ?, ?, ?, ?, 'haiku-4.5', 'PARKED', 'timeout')
        """,
        (
            _JOURNAL,
            _ANCHOR,
            _TEXT,
            compute_entry_content_sha256(_TEXT),
            PROMPT_REV_SOFT_V2,
        ),
    )
    conn.commit()

    with patch("cortex_store.digest_jobs.cortex_conn") as mock_conn:
        mock_conn.side_effect = lambda: conn
        result = retry_job("job-1")

    assert result["state"] == "ENQUEUED"
    row = conn.execute("SELECT state FROM digest_jobs WHERE job_id = 'job-1'").fetchone()
    assert row["state"] == "ENQUEUED"


@pytest.mark.offline
def test_job_status(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO digest_jobs (
            job_id, kind, journal_entity_id, entry_anchor, entry_text,
            content_sha256, prompt_rev, model, state
        ) VALUES ('job-2', 'extract', ?, ?, ?, ?, ?, 'haiku-4.5', 'ENQUEUED')
        """,
        (
            _JOURNAL,
            _ANCHOR,
            _TEXT,
            compute_entry_content_sha256(_TEXT),
            PROMPT_REV_SOFT_V2,
        ),
    )
    conn.commit()

    with patch("cortex_store.digest_jobs.cortex_conn") as mock_conn:
        mock_conn.side_effect = lambda: conn
        result = job_status("job-2")

    assert result["status"] == "ok"
    assert result["job"]["job_id"] == "job-2"


@pytest.mark.offline
def test_concurrent_claim_yields_single_cdp_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent manage + MCP tick on one ENQUEUED job → one CDP submit."""
    from concurrent.futures import ThreadPoolExecutor

    shared = sqlite3.connect(
        "file:digest_jobs_concurrent?mode=memory&cache=shared",
        uri=True,
        check_same_thread=False,
    )
    shared.row_factory = sqlite3.Row
    migration_068.migrate(shared)
    migration_071.migrate(shared)
    migration_072.migrate(shared)

    monkeypatch.setenv("CORTEX_DIGEST_CDP_PROJECT_UUID", "proj-test")

    def _conn() -> sqlite3.Connection:
        conn = sqlite3.connect(
            "file:digest_jobs_concurrent?mode=memory&cache=shared",
            uri=True,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        return conn

    with patch("cortex_store.digest_jobs.cortex_conn", side_effect=_conn):
        enq = enqueue_extract(
            journal_entity_id=_JOURNAL,
            entry_anchor=_ANCHOR,
            entry_text=_TEXT,
        )

    cdp_calls: list[str] = []
    success = {
        "execution_id": "exec-1",
        "prompt_uri": "cortex://ephemeral/digest/prompt.md",
        "prompt_sha256": "sha256:abc",
        "archive_uri": "cortex://ephemeral/digest/archive.md",
        "harvest_sha256": "sha256:harvest",
        "claims_json": {"claims": [{"id": "c1"}]},
    }

    def fake_cdp(job: dict) -> dict:
        cdp_calls.append(str(job["job_id"]))
        return success

    with (
        patch("cortex_store.digest_jobs.cortex_conn", side_effect=_conn),
        patch("cortex_store.digest_jobs.run_cdp_extract_for_job", side_effect=fake_cdp),
        patch(
            "cortex_store.dispatch_ops.ops_digest.finish_claim_batch_for_job",
            return_value={
                "ledger_id": 1,
                "staging_batch_id": "batch-1",
                "claims_json": success["claims_json"],
                "verify_verdicts": {},
            },
        ),
    ):
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(tick_jobs, limit=1)
            f2 = pool.submit(tick_jobs, limit=1)
            f1.result()
            f2.result()

    assert len(cdp_calls) == 1
    assert cdp_calls[0] == enq["job_id"]
    row = shared.execute(
        "SELECT state FROM digest_jobs WHERE job_id = ?",
        (enq["job_id"],),
    ).fetchone()
    assert row["state"] == "STAGED"
    shared.close()
