"""Slice-2 dynamic predicate tests — AC11–AC13, A7, expire, schedule refuse."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.trigger_service.models import (
    PREDICATE_TRIGGER_TERMINAL,
    STATUS_EXPIRED,
    STATUS_FIRING,
    STATUS_SCHEDULED,
    TriggerStoreError,
)
from services.git_integration_worker.trigger_service.store import TriggerStore

_PROMPT = "cortex://notes/system/threads/test-prompt.md"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TriggerStore:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    return TriggerStore()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setenv("PROJECT_ASK_URL", "http://127.0.0.1:8770")
    from agent_bus_store.auth import require_token

    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def _schedule_basic(
    store: TriggerStore,
    *,
    fire_at: datetime,
    prompt_uri: str = _PROMPT,
    **kwargs,
):
    return store.schedule(
        created_by="test",
        fire_at=fire_at,
        prompt_uri=prompt_uri,
        **kwargs,
    )


def test_migration_002_preserves_preexisting_rows_and_indexes(tmp_path: Path) -> None:
    """AC1 / F2 — rows and 001 indexes must survive the 002 rebuild."""
    from services.git_integration_worker.trigger_service.migrations.migration_001_triggers import (
        migrate as migrate_001,
    )
    from services.git_integration_worker.trigger_service.migrations.migration_002_predicates import (
        migrate as migrate_002,
    )

    db_file = tmp_path / "pre-002.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    migrate_001(conn)
    now = datetime.now(UTC)
    scheduled_id = "sched-" + "a" * 26
    fired_id = "fired-" + "b" * 26
    conn.execute(
        """
        INSERT INTO triggers (
            id, created_at, created_by, fire_at, prompt_uri,
            purpose, model, status, attempts, max_attempts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scheduled_id,
            now.isoformat(),
            "test",
            (now + timedelta(hours=1)).isoformat(),
            _PROMPT,
            "operator-proxy",
            "opus-5",
            STATUS_SCHEDULED,
            0,
            3,
        ),
    )
    conn.execute(
        """
        INSERT INTO triggers (
            id, created_at, created_by, fire_at, prompt_uri,
            purpose, model, status, attempts, max_attempts,
            execution_id, fired_at, terminal_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fired_id,
            now.isoformat(),
            "test",
            (now - timedelta(hours=1)).isoformat(),
            _PROMPT,
            "operator-proxy",
            "opus-5",
            "fired",
            1,
            3,
            "exec-survive",
            now.isoformat(),
            "completed",
        ),
    )
    conn.commit()
    count_before = conn.execute("SELECT COUNT(*) FROM triggers").fetchone()[0]
    indexes_before = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='triggers'"
        )
    }
    scheduled_before = dict(
        conn.execute("SELECT * FROM triggers WHERE id = ?", (scheduled_id,)).fetchone()
    )
    fired_before = dict(
        conn.execute("SELECT * FROM triggers WHERE id = ?", (fired_id,)).fetchone()
    )

    migrate_002(conn)

    count_after = conn.execute("SELECT COUNT(*) FROM triggers").fetchone()[0]
    indexes_after = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='triggers'"
        )
    }
    assert count_after == count_before == 2
    assert "idx_triggers_status_fire_at" in indexes_after
    assert "idx_triggers_fired_reconcile" in indexes_after
    assert indexes_before <= indexes_after
    cols = {r[1] for r in conn.execute("PRAGMA table_info(triggers)")}
    assert {"predicate", "predicate_args", "expires_at", "last_predicate_error"} <= cols
    scheduled_after = dict(
        conn.execute("SELECT * FROM triggers WHERE id = ?", (scheduled_id,)).fetchone()
    )
    fired_after = dict(
        conn.execute("SELECT * FROM triggers WHERE id = ?", (fired_id,)).fetchone()
    )
    for key, value in scheduled_before.items():
        assert scheduled_after[key] == value
    for key, value in fired_before.items():
        assert fired_after[key] == value
    assert fired_after["execution_id"] == "exec-survive"
    assert fired_after["terminal_status"] == "completed"

    # F1 re-run safety — must not wedge on triggers_new already exists
    migrate_002(conn)
    assert conn.execute("SELECT COUNT(*) FROM triggers").fetchone()[0] == 2
    conn.close()


def test_schedule_unknown_predicate_refused(store: TriggerStore) -> None:
    upstream = _schedule_basic(
        store,
        fire_at=datetime.now(UTC) + timedelta(hours=1),
    )
    with pytest.raises(TriggerStoreError, match="unknown predicate"):
        store.schedule(
            created_by="test",
            fire_at=datetime.now(UTC) + timedelta(hours=2),
            prompt_uri=_PROMPT,
            predicate="cel_expr",
            predicate_args={"trigger_id": upstream.id},
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )


def test_schedule_missing_expires_at_refused(store: TriggerStore) -> None:
    upstream = _schedule_basic(
        store,
        fire_at=datetime.now(UTC) + timedelta(hours=1),
    )
    with pytest.raises(TriggerStoreError, match="expires_at required"):
        store.schedule(
            created_by="test",
            fire_at=datetime.now(UTC) + timedelta(hours=2),
            prompt_uri=_PROMPT,
            predicate=PREDICATE_TRIGGER_TERMINAL,
            predicate_args={"trigger_id": upstream.id},
        )


def test_schedule_unresolvable_upstream_refused(store: TriggerStore) -> None:
    with pytest.raises(TriggerStoreError, match="does not exist"):
        store.schedule(
            created_by="test",
            fire_at=datetime.now(UTC) + timedelta(hours=1),
            prompt_uri=_PROMPT,
            predicate=PREDICATE_TRIGGER_TERMINAL,
            predicate_args={"trigger_id": "nonexistent-id"},
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )


def test_trigger_terminal_chaining(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    upstream = _schedule_basic(store, fire_at=now - timedelta(minutes=10))
    claimed_a = store.claim_due(now=now)
    assert claimed_a is not None
    store.mark_fired(upstream.id, execution_id="exec-a")
    store.mark_reconciled(upstream.id, terminal_status="completed")

    downstream = store.schedule(
        created_by="test",
        fire_at=now - timedelta(minutes=5),
        prompt_uri=_PROMPT,
        predicate=PREDICATE_TRIGGER_TERMINAL,
        predicate_args={"trigger_id": upstream.id},
        expires_at=now + timedelta(hours=1),
    )
    claimed_b = store.claim_due(now=now)
    assert claimed_b is not None
    assert claimed_b.id == downstream.id
    assert claimed_b.status == STATUS_FIRING


def test_expired_before_upstream_terminals(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    upstream = _schedule_basic(store, fire_at=now + timedelta(hours=1))
    downstream = store.schedule(
        created_by="test",
        fire_at=now - timedelta(minutes=30),
        prompt_uri=_PROMPT,
        predicate=PREDICATE_TRIGGER_TERMINAL,
        predicate_args={"trigger_id": upstream.id},
        expires_at=now - timedelta(minutes=1),
    )
    expired = store.expire_due(now=now)
    assert len(expired) == 1
    assert expired[0].id == downstream.id
    assert expired[0].status == STATUS_EXPIRED
    assert store.claim_due(now=now) is None
    row = store.get(downstream.id)
    assert row is not None
    assert row.status == STATUS_EXPIRED
    assert row.attempts == 0


def test_predicate_null_regression(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    row = _schedule_basic(store, fire_at=now - timedelta(seconds=30))
    claimed = store.claim_due(now=now)
    assert claimed is not None
    assert claimed.id == row.id
    assert claimed.status == STATUS_FIRING


def test_ac11_skip_not_block_head_of_line(store: TriggerStore) -> None:
    """Predicate row at fire_at-60s blocks NULL row at fire_at-30s — must skip."""
    now = datetime.now(UTC)
    upstream = _schedule_basic(store, fire_at=now + timedelta(hours=1))
    store.schedule(
        created_by="test",
        fire_at=now - timedelta(seconds=60),
        prompt_uri=_PROMPT,
        predicate=PREDICATE_TRIGGER_TERMINAL,
        predicate_args={"trigger_id": upstream.id},
        expires_at=now + timedelta(hours=1),
    )
    null_row = _schedule_basic(store, fire_at=now - timedelta(seconds=30))
    claimed = store.claim_due(now=now)
    assert claimed is not None
    assert claimed.id == null_row.id
    assert claimed.status == STATUS_FIRING


def test_ac12_truth_at_schedule(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    upstream = _schedule_basic(store, fire_at=now - timedelta(minutes=10))
    store.claim_due(now=now)
    store.mark_fired(upstream.id, execution_id="exec-up")
    store.mark_reconciled(upstream.id, terminal_status="completed")
    downstream = store.schedule(
        created_by="test",
        fire_at=now - timedelta(minutes=1),
        prompt_uri=_PROMPT,
        predicate=PREDICATE_TRIGGER_TERMINAL,
        predicate_args={"trigger_id": upstream.id},
        expires_at=now + timedelta(hours=1),
    )
    claimed = store.claim_due(now=now)
    assert claimed is not None
    assert claimed.id == downstream.id


def test_mark_reconciled_write_guard_idempotent(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    row = _schedule_basic(store, fire_at=now - timedelta(minutes=5))
    claimed = store.claim_due(now=now)
    assert claimed is not None
    store.mark_fired(row.id, execution_id="exec-1")
    store.mark_reconciled(row.id, terminal_status="completed")
    before = store.get(row.id)
    assert before is not None
    assert before.terminal_status == "completed"
    store.mark_reconciled(row.id, terminal_status="failed")
    after = store.get(row.id)
    assert after is not None
    assert after.terminal_status == "completed"


def test_a7_terminal_status_monotonic_under_reclaim(store: TriggerStore) -> None:
    """A7 / F4 — reclaim + submit-retry must not mutate a reconciled terminal_status."""
    now = datetime.now(UTC)
    row = _schedule_basic(store, fire_at=now - timedelta(minutes=5))
    claimed = store.claim_due(now=now)
    assert claimed is not None
    store.mark_fired(row.id, execution_id="exec-1")
    store.mark_reconciled(row.id, terminal_status="completed")
    store.reclaim_stale_firing(now=now, stale_after_s=0)
    store.mark_submit_retry(row.id, error="should-noop", attempts=1, max_attempts=3)
    after = store.get(row.id)
    assert after is not None
    assert after.status == "fired"
    assert after.terminal_status == "completed"
    assert after.execution_id == "exec-1"


def test_expire_preempts_retries(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    row = _schedule_basic(store, fire_at=now - timedelta(hours=1))
    claimed = store.claim_due(now=now)
    assert claimed is not None
    store.mark_submit_retry(row.id, error="transient", attempts=2, max_attempts=3)
    updated = store.get(row.id)
    assert updated is not None
    assert updated.attempts == 2
    with store._connect() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE triggers SET expires_at = ? WHERE id = ?",
            ((now - timedelta(minutes=1)).isoformat(), row.id),
        )
        conn.commit()
    expired = store.expire_due(now=now)
    assert any(r.id == row.id for r in expired)
    final = store.get(row.id)
    assert final is not None
    assert final.status == STATUS_EXPIRED


def test_route_unknown_predicate_422(client: TestClient) -> None:
    fire_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with patch(
        "services.git_integration_worker.routes.triggers.publish_lib_signal",
    ):
        upstream = client.post(
            "/api/v1/triggers",
            json={"fire_at": fire_at, "prompt_text": "upstream"},
        )
    assert upstream.status_code == 200
    upstream_id = upstream.json()["id"]
    resp = client.post(
        "/api/v1/triggers",
        json={
            "fire_at": fire_at,
            "prompt_text": "downstream",
            "predicate": "unknown_type",
            "predicate_args": {"trigger_id": upstream_id},
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["reason_code"] == "unknown_predicate_type"


def test_route_unresolvable_upstream_422(client: TestClient) -> None:
    fire_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    resp = client.post(
        "/api/v1/triggers",
        json={
            "fire_at": fire_at,
            "prompt_text": "downstream",
            "predicate": PREDICATE_TRIGGER_TERMINAL,
            "predicate_args": {"trigger_id": "missing-upstream"},
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason_code"] == "unresolvable_upstream_trigger_id"


def test_ac13_list_surfaces_predicate_fields(client: TestClient) -> None:
    fire_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    expires = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    with patch(
        "services.git_integration_worker.routes.triggers.publish_lib_signal",
    ):
        upstream = client.post(
            "/api/v1/triggers",
            json={"fire_at": fire_at, "prompt_text": "upstream"},
        )
        assert upstream.status_code == 200
        downstream = client.post(
            "/api/v1/triggers",
            json={
                "fire_at": fire_at,
                "prompt_text": "chain",
                "predicate": PREDICATE_TRIGGER_TERMINAL,
                "predicate_args": {"trigger_id": upstream.json()["id"]},
                "expires_at": expires,
            },
        )
    assert downstream.status_code == 200
    body = downstream.json()
    assert body["predicate"] == PREDICATE_TRIGGER_TERMINAL
    assert body["predicate_args"]["trigger_id"] == upstream.json()["id"]
    assert body["expires_at"] == expires

    listed = client.get("/api/v1/triggers")
    assert listed.status_code == 200
    found = next(t for t in listed.json()["triggers"] if t["id"] == body["id"])
    assert found["predicate"] == PREDICATE_TRIGGER_TERMINAL
    assert found["expires_at"] == expires


def test_predicate_true_event_on_claim(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    upstream = _schedule_basic(store, fire_at=now - timedelta(minutes=10))
    store.claim_due(now=now)
    store.mark_fired(upstream.id, execution_id="e1")
    store.mark_reconciled(upstream.id, terminal_status="completed")
    store.schedule(
        created_by="test",
        fire_at=now - timedelta(minutes=1),
        prompt_uri=_PROMPT,
        predicate=PREDICATE_TRIGGER_TERMINAL,
        predicate_args={"trigger_id": upstream.id},
        expires_at=now + timedelta(hours=1),
    )
    emitted: list[tuple[str, dict]] = []

    def capture(signal: str, payload: dict) -> None:
        emitted.append((signal, payload))

    store.claim_due(now=now, _emit=capture)
    assert any(s == "giw.trigger.predicate_true" for s, _ in emitted)


def test_expired_rows_transition(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    upstream = _schedule_basic(store, fire_at=now - timedelta(hours=2))
    downstream_id = store.schedule(
        created_by="test",
        fire_at=now - timedelta(minutes=30),
        prompt_uri=_PROMPT,
        predicate=PREDICATE_TRIGGER_TERMINAL,
        predicate_args={"trigger_id": upstream.id},
        expires_at=now - timedelta(minutes=1),
    ).id
    expired = store.expire_due(now=now)
    assert any(r.id == downstream_id for r in expired)


def test_expired_event_emitted_on_expire(store: TriggerStore) -> None:
    """F6 — expire_due emits giw.trigger.expired post-commit (exact string)."""
    now = datetime.now(UTC)
    upstream = _schedule_basic(store, fire_at=now + timedelta(hours=1))
    downstream = store.schedule(
        created_by="test",
        fire_at=now - timedelta(minutes=30),
        prompt_uri=_PROMPT,
        predicate=PREDICATE_TRIGGER_TERMINAL,
        predicate_args={"trigger_id": upstream.id},
        expires_at=now - timedelta(minutes=1),
    )
    emitted: list[tuple[str, dict]] = []

    def capture(signal: str, payload: dict) -> None:
        emitted.append((signal, payload))

    store.expire_due(now=now, _emit=capture)
    assert any(s == "giw.trigger.expired" for s, _ in emitted)
    match = next(p for s, p in emitted if s == "giw.trigger.expired")
    assert match["trigger_id"] == downstream.id


def test_naive_datetime_treated_as_utc(store: TriggerStore) -> None:
    """F7 — tz-naive fire_at/expires_at are stored as UTC, not system-local."""
    naive_fire = datetime(2030, 1, 15, 12, 0, 0)  # noqa: DTZ001 — intentional naive
    row = _schedule_basic(store, fire_at=naive_fire)
    assert row.fire_at.startswith("2030-01-15T12:00:00")
    assert "+00:00" in row.fire_at or row.fire_at.endswith("Z")
    upstream = row
    naive_expires = datetime(2030, 1, 16, 12, 0, 0)  # noqa: DTZ001
    downstream = store.schedule(
        created_by="test",
        fire_at=naive_fire + timedelta(hours=1),
        prompt_uri=_PROMPT,
        predicate=PREDICATE_TRIGGER_TERMINAL,
        predicate_args={"trigger_id": upstream.id},
        expires_at=naive_expires,
    )
    assert downstream.expires_at is not None
    assert downstream.expires_at.startswith("2030-01-16T12:00:00")


def test_require_status_rejects_unknown() -> None:
    """F5 — ALL_STATUSES is enforced at the write boundary."""
    from services.git_integration_worker.trigger_service.models import require_status

    assert require_status(STATUS_SCHEDULED) == STATUS_SCHEDULED
    with pytest.raises(TriggerStoreError, match="invalid trigger status"):
        require_status("not-a-status")
