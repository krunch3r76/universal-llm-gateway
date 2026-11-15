"""Archive verdict salvage at cdp_ask finish_execution (a:27142)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cdp_ask.app import create_app
from cdp_ask.execution_ladder import finish_execution
from cdp_ask.execution_store import ExecutionStore

pytestmark = pytest.mark.offline

_882FEF0D_ARCHIVE = """# CDP ask harvest

- execution_id: `3a138d9c3ec640d9953ca5c1e9c5f451`
- stargate_execution_id: `882fef0d-ebd1-4cce-9605-be2243f114a2`
- outcome: failed
- error: skill_receipt_unverified: required skills fail channel attest after attach + inline + induction seal: undelivered=['reasoning-posture', 'consult-posture', 'hypothesize-simulate'] (attached=[], inlined=[], induction=[]) — fail closed (friction a:27142)

## Body

VERDICT: APPROVE_WITH_NITS range=706f4b8dbbb376bdbf3ee62ec3b77c8f62c303db..d2bf6021088fb85eac2d30c5412bc5811ba2fba9 head=d2bf6021088fb85eac2d30c5412bc5811ba2fba9

Seat review complete.
"""


@pytest.fixture
def store() -> ExecutionStore:
    return ExecutionStore()


@pytest.mark.asyncio
async def test_finish_execution_promotes_skill_receipt_archive_verdict(
    store: ExecutionStore, tmp_path: Path
) -> None:
    """End-to-end through finish_execution with a real archived harvest body."""
    record = await store.create(holder="test", purpose="review")
    err = (
        "skill_receipt_unverified: undelivered=['reasoning-posture'] (friction a:27142)"
    )
    archive = tmp_path / "882fef0d-archive.md"
    archive.write_text(_882FEF0D_ARCHIVE, encoding="utf-8")
    payload = {
        "ok": False,
        "error": err,
        "body": "harvested review prose",
        "archive_uri": f"file://{archive}",
        "stall_stage": "observer_unverified",
    }
    await finish_execution(store, record.execution_id, payload)

    refreshed = await store.get(record.execution_id)
    assert refreshed is not None
    assert refreshed.status == "completed"
    result = refreshed.result or {}
    assert result.get("ok") is True
    assert result.get("archive_verdict") == "APPROVE_WITH_NITS"
    assert result.get("skill_receipt_warning") == err
    assert result.get("harvest_provenance") == "chat"
    assert refreshed.error is None


@pytest.mark.asyncio
async def test_poll_surfaces_salvage_fields(
    store: ExecutionStore, tmp_path: Path
) -> None:
    record = await store.create(holder="test", purpose="review")
    err = "skill_receipt_unverified: undelivered=['reasoning-posture']"
    archive = tmp_path / "poll-archive.md"
    archive.write_text(_882FEF0D_ARCHIVE, encoding="utf-8")
    await finish_execution(
        store,
        record.execution_id,
        {
            "ok": False,
            "error": err,
            "body": "harvested",
            "archive_uri": f"file://{archive}",
            "stall_stage": "observer_unverified",
        },
    )
    client = TestClient(create_app(store=store))
    poll = client.get(f"/v1/project-ask/executions/{record.execution_id}").json()
    assert poll["ok"] is True
    assert poll["archive_verdict"] == "APPROVE_WITH_NITS"
    assert poll["verdict_source"] == "archive"
    assert poll["skill_receipt_warning"] == err
    assert err in (poll.get("warnings") or [])
