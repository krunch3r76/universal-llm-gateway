"""AC1/AC2/AC5: trigger store schema, claim, prompt snapshot."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.git_integration_worker.trigger_service.models import TriggerStoreError
from services.git_integration_worker.trigger_service.store import TriggerStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TriggerStore:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "CORTEX_FILES_ROOT",
        str(tmp_path / "cortex"),
    )
    return TriggerStore()


def test_schema_migrations_and_wal(store: TriggerStore, tmp_path: Path) -> None:
    db_path = tmp_path / "trigger-schedule.sqlite"
    assert db_path.is_file()
    conn = sqlite3.connect(str(db_path))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"
    applied = store._connect().execute(  # noqa: SLF001
        "SELECT id FROM schema_migrations"
    ).fetchall()
    assert any(row[0] == "001_triggers" for row in applied)
    assert any(row[0] == "002_predicates" for row in applied)
    assert any(row[0] == "003_act_receipt" for row in applied)
    assert any(row[0] == "004_story_envelope" for row in applied)


def test_prompt_text_snapshotted_to_cortex_uri(
    store: TriggerStore,
    tmp_path: Path,
) -> None:
    fire_at = datetime.now(UTC) + timedelta(minutes=5)
    row = store.schedule(
        created_by="test-seat",
        fire_at=fire_at,
        prompt_text="follow up on mission",
        arc="agent-bus:6230",
    )
    assert row.prompt_uri.startswith("cortex://")
    assert "trigger-schedule" in row.prompt_uri
    rel = row.prompt_uri.removeprefix("cortex://")
    prompt_file = tmp_path / "cortex" / rel
    assert prompt_file.is_file()
    assert prompt_file.read_text(encoding="utf-8") == "follow up on mission"


def test_claim_due_exactly_once_under_concurrency(store: TriggerStore) -> None:
    fire_at = datetime.now(UTC) - timedelta(seconds=1)
    row = store.schedule(
        created_by="test",
        fire_at=fire_at,
        prompt_uri="cortex://notes/system/threads/test-prompt.md",
    )
    results: list[str | None] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait()
        claimed = store.claim_due()
        results.append(claimed.id if claimed else None)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert results.count(row.id) == 1
    assert results.count(None) == 1


def test_cancel_fired_refused(store: TriggerStore) -> None:
    fire_at = datetime.now(UTC) - timedelta(seconds=1)
    row = store.schedule(
        created_by="test",
        fire_at=fire_at,
        prompt_uri="cortex://notes/system/threads/test-prompt.md",
    )
    claimed = store.claim_due()
    assert claimed is not None
    store.mark_fired(row.id, execution_id="exec-1")
    with pytest.raises(TriggerStoreError, match="cannot cancel a fired"):
        store.cancel(row.id)
