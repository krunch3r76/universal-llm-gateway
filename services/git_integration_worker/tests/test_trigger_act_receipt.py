"""Act receipt migration, tri-state require, and reconcile verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from claude_bundles.act_receipt import format_act_receipt
from services.git_integration_worker.trigger_service.act_verify import (
    ACT_STATUS_CLAIMED,
    ACT_STATUS_PENDING,
    ACT_STATUS_UNVERIFIED,
    ACT_STATUS_VERIFIED,
    CallableEvidenceResolver,
    effective_require_act,
    verify_act_for_row,
    write_dedicated_receipt,
)
from services.git_integration_worker.trigger_service.fire import reconcile_row
from services.git_integration_worker.trigger_service.store import TriggerStore


@pytest.fixture
def store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TriggerStore:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    return TriggerStore()


def test_migration_003_applied(store: TriggerStore) -> None:
    applied = store._connect().execute(  # noqa: SLF001
        "SELECT id FROM schema_migrations"
    ).fetchall()
    assert any(row[0] == "003_act_receipt" for row in applied)
    cols = {
        row[1]
        for row in store._connect().execute("PRAGMA table_info(triggers)").fetchall()  # noqa: SLF001
    }
    assert {"act_status", "act_evidence_uri", "act_error", "require_act_receipt"} <= cols


def test_schedule_convenience_default_require_act(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) + timedelta(minutes=1),
        prompt_uri="cortex://notes/system/threads/p.md",
        purpose="operator-proxy",
    )
    assert row.require_act_receipt == 1


def test_effective_require_act_tri_state(store: TriggerStore) -> None:
    explicit_waive = store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) + timedelta(minutes=1),
        prompt_uri="cortex://notes/system/threads/p.md",
        purpose="operator-proxy",
        require_act_receipt=0,
        _require_act_explicit=True,
    )
    assert not effective_require_act(explicit_waive)
    derived = store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) + timedelta(minutes=1),
        prompt_uri="cortex://notes/system/threads/p.md",
        purpose="operator-proxy",
        require_act_receipt=None,
        _require_act_explicit=True,
    )
    assert effective_require_act(derived)


def test_a1_falsifier_nonexistent_evidence_not_verified(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) - timedelta(seconds=1),
        prompt_uri="cortex://notes/system/threads/p.md",
    )
    claimed = store.claim_due()
    assert claimed is not None
    receipt = format_act_receipt(
        commission_kind="agent_bus_request",
        evidence_uri="cortex://notes/system/ephemeral/missing-evidence.md",
        trigger_id=claimed.id,
    )
    write_dedicated_receipt(claimed.id, receipt)
    fired = store.mark_fired(claimed.id, execution_id="exec-1")
    reconciled = store.mark_reconciled(
        fired.id,
        terminal_status="completed",
        archive_uri=None,
    )
    result = verify_act_for_row(reconciled)
    assert result["act_status"] in {ACT_STATUS_CLAIMED, ACT_STATUS_UNVERIFIED}
    assert result["act_status"] != ACT_STATUS_VERIFIED
    assert result["event"] == "giw.trigger.act_unverified"


def test_reader_failure_pending_not_unverified(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) - timedelta(seconds=1),
        prompt_uri="cortex://notes/system/threads/p.md",
    )
    claimed = store.claim_due()
    assert claimed is not None
    fired = store.mark_fired(claimed.id, execution_id="exec-2")
    reconciled = store.mark_reconciled(
        fired.id,
        terminal_status="completed",
        archive_uri="cortex://notes/system/ephemeral/missing-archive.md",
    )
    result = verify_act_for_row(reconciled)
    assert result["act_status"] == ACT_STATUS_PENDING
    assert result["event"] is None


def test_verified_when_resolver_confirms(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) - timedelta(seconds=1),
        prompt_uri="cortex://notes/system/threads/p.md",
    )
    claimed = store.claim_due()
    assert claimed is not None
    evidence = "cortex://notes/system/ephemeral/evidence.md"
    receipt = format_act_receipt(
        commission_kind="agent_bus_request",
        evidence_uri=evidence,
        trigger_id=claimed.id,
    )
    write_dedicated_receipt(claimed.id, receipt)
    fired = store.mark_fired(claimed.id, execution_id="exec-3")
    reconciled = store.mark_reconciled(
        fired.id,
        terminal_status="completed",
    )
    resolver = CallableEvidenceResolver(lambda r, _row, _at: (True, None))
    result = verify_act_for_row(reconciled, resolver=resolver)
    assert result["act_status"] == ACT_STATUS_VERIFIED
    assert result["event"] == "giw.trigger.act_verified"


def test_missing_receipt_unverified_no_second_submit(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) - timedelta(seconds=1),
        prompt_uri="cortex://notes/system/threads/p.md",
    )
    claimed = store.claim_due()
    assert claimed is not None
    client = MagicMock()
    client._request.return_value = {"at_hard_limit": False}
    client.submit.return_value = {"execution_id": "exec-4", "status": "running"}
    client.poll.return_value = {"status": "completed"}
    with patch(
        "services.git_integration_worker.trigger_service.fire.lane_available",
        return_value=(True, None),
    ), patch(
        "services.git_integration_worker.trigger_service.fire.publish_lib_signal",
    ):
        from services.git_integration_worker.trigger_service.fire import fire_once

        fired = fire_once(store, claimed, client=client)
    with patch(
        "services.git_integration_worker.trigger_service.fire.publish_lib_signal",
    ):
        reconciled = reconcile_row(store, fired, client=client)
    assert reconciled is not None
    assert reconciled.act_status == ACT_STATUS_UNVERIFIED
    assert client.submit.call_count == 1
