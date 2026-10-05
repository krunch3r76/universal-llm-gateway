"""CDP step lineage: one pipeline leg, poll on continuation, no thread delivery."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from claude_bundles.cdp_model_endpoint import CdpGenerateResult

from systems.frontier_consult import cdp_generate_reconcile as reconcile
from systems.frontier_consult.cdp_generate_inflight_ledger import (
    _db_path,
    read_inflight_leg,
    upsert_inflight_leg,
)
from systems.frontier_consult.cdp_generate_reconcile import (
    max_open_leg_s,
    reset_cdp_generate_reconcile_for_tests,
)
from systems.pipeline.core.handlers.frontier_dispatch import (
    cdp_dispatch as cdp_mod,
)
from systems.pipeline.core.handlers.frontier_dispatch.cdp_dispatch import (
    run_cdp_dispatch,
)
from systems.pipeline.core.handlers.pipeline_context import PipelineContext

_OLD_LEDGER_DDL = """
CREATE TABLE cdp_inflight_leg (
    execution_id              TEXT PRIMARY KEY,
    request_id                TEXT NOT NULL,
    satellite_execution_id    TEXT,
    thread_id                 TEXT NOT NULL,
    pointer_turn              INTEGER NOT NULL DEFAULT 1,
    caller_agent              TEXT,
    prompt_uri                TEXT NOT NULL,
    model_id                  TEXT NOT NULL,
    max_wall_s                REAL NOT NULL,
    admitted_at               TEXT NOT NULL,
    proof_emitted             INTEGER NOT NULL DEFAULT 0,
    delivered                 INTEGER NOT NULL DEFAULT 0,
    finalize_claim_until      TEXT,
    finalize_claim_holder     TEXT,
    abandoned                 INTEGER NOT NULL DEFAULT 0,
    purpose                   TEXT
);
"""


@pytest.fixture(autouse=True)
def _isolate_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    reset_cdp_generate_reconcile_for_tests()


def _step() -> SimpleNamespace:
    return SimpleNamespace(id="respond")


def _admission() -> SimpleNamespace:
    return SimpleNamespace(
        model="cdp/opus-5",
        model_entity_id="model:cdp-opus-5",
        user_prompt="question",
        system=None,
        opts={},
        publish=lambda _event: None,
    )


class _Run:
    """Duck context that uses S5's real ``step_idempotency_key``."""

    step_idempotency_key = PipelineContext.step_idempotency_key

    def __init__(self, execution_id: str, lineage_root: str | None) -> None:
        self.execution_id = execution_id
        self.lineage_root = lineage_root


def _proof_snapshot() -> dict[str, Any]:
    return {
        "status": "completed",
        "completion_phase": "terminal",
        "archive_uri": "cortex://notes/system/artifacts/cdp/proof.md",
        "body": "harvest",
        "attested_model": "Model: Opus 5",
    }


def _ok_generate(**kwargs: Any) -> CdpGenerateResult:
    on_submitted = kwargs.get("on_submitted")
    if on_submitted is not None:
        on_submitted("sat-fresh")
    return CdpGenerateResult(
        ok=True,
        body="fresh",
        execution_id=kwargs["execution_id"],
        satellite_execution_id="sat-fresh",
        prompt_uri="cortex://p.md",
        picker_model="opus-5",
    )


@pytest.mark.asyncio
async def test_killed_run_records_pipeline_leg_and_continuation_polls_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restart mid-generate: the continuation polls the open leg and does not submit."""
    generate_calls: list[dict[str, Any]] = []

    def killed_generate(**kwargs: Any) -> CdpGenerateResult:
        generate_calls.append(kwargs)
        kwargs["on_submitted"]("sat-killed")
        raise RuntimeError("killed")

    monkeypatch.setattr(cdp_mod, "run_cdp_generate", killed_generate)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_events.publish_cdp_kwargs",
        lambda *_a, **_k: None,
    )
    step = _step()
    first = _Run("exec-1", "root-1")
    with pytest.raises(RuntimeError, match="killed"):
        await run_cdp_dispatch(
            handler=SimpleNamespace(),
            step=step,
            context=first,
            admission=_admission(),
        )

    leg = read_inflight_leg("root-1:respond")
    assert leg is not None
    assert leg.owner == "pipeline"
    assert leg.satellite_execution_id == "sat-killed"
    assert len(generate_calls) == 1

    polls: list[str] = []

    async def fake_poll(satellite_execution_id: str) -> dict[str, Any]:
        polls.append(satellite_execution_id)
        return _proof_snapshot()

    def refuse_submit(**_kwargs: Any) -> CdpGenerateResult:
        raise AssertionError("continuation must not submit")

    monkeypatch.setattr(cdp_mod, "run_cdp_generate", refuse_submit)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.poll_satellite_snapshot",
        fake_poll,
    )

    out = await run_cdp_dispatch(
        handler=SimpleNamespace(),
        step=step,
        context=_Run("exec-2", "root-1"),
        admission=_admission(),
    )

    assert polls == ["sat-killed"]
    assert len(generate_calls) == 1
    assert out.raw == "harvest"
    assert out.json is not None
    assert out.json["archive_uri"] == "cortex://notes/system/artifacts/cdp/proof.md"


@pytest.mark.asyncio
async def test_no_lineage_root_submits_once_per_run_without_shared_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chat-path runs have no pinned root. Each submits once under its own key.

    Covers empty and ``None`` lineage_root. A shared ``""`` or ``None`` key
    would make one run poll the other's CDP leg.
    """
    calls: list[str] = []

    def fake_generate(**kwargs: Any) -> CdpGenerateResult:
        calls.append(kwargs["execution_id"])
        return _ok_generate(**kwargs)

    monkeypatch.setattr(cdp_mod, "run_cdp_generate", fake_generate)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_events.publish_cdp_kwargs",
        lambda *_a, **_k: None,
    )
    step = _step()
    await run_cdp_dispatch(
        handler=SimpleNamespace(),
        step=step,
        context=_Run("exec-a", ""),
        admission=_admission(),
    )
    await run_cdp_dispatch(
        handler=SimpleNamespace(),
        step=step,
        context=_Run("exec-b", None),
        admission=_admission(),
    )

    assert calls == ["exec-a", "exec-b"]
    leg_a = read_inflight_leg("exec-a:respond")
    leg_b = read_inflight_leg("exec-b:respond")
    assert leg_a is not None and leg_a.owner == "pipeline"
    assert leg_b is not None and leg_b.owner == "pipeline"
    assert leg_a.execution_id != leg_b.execution_id
    assert read_inflight_leg(":respond") is None
    assert read_inflight_leg("None:respond") is None


@pytest.mark.asyncio
async def test_reconcile_does_not_deliver_pipeline_owner_leg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deliver = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        deliver,
    )
    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", lambda *_a, **_k: None)
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(return_value=_proof_snapshot()),
    )
    upsert_inflight_leg(
        execution_id="root-1:respond",
        request_id="exec-1",
        thread_id="",
        pointer_turn=1,
        caller_agent=None,
        prompt_uri="pipeline://root-1:respond",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
        owner="pipeline",
    )
    reconcile.attach_satellite_execution_id(
        execution_id="root-1:respond",
        satellite_execution_id="sat-pipe",
    )

    await reconcile.reconcile_cdp_inflight_legs()

    deliver.assert_not_called()
    leg = read_inflight_leg("root-1:respond")
    assert leg is not None
    assert leg.proof_emitted is False
    assert leg.delivered is False
    assert leg.abandoned is False


@pytest.mark.asyncio
async def test_reconcile_pipeline_owner_abandons_at_horizon_without_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deliver = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        deliver,
    )
    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", lambda *_a, **_k: None)
    upsert_inflight_leg(
        execution_id="root-h:respond",
        request_id="exec-h",
        thread_id="",
        pointer_turn=1,
        caller_agent=None,
        prompt_uri="pipeline://root-h:respond",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
        owner="pipeline",
    )
    reconcile.attach_satellite_execution_id(
        execution_id="root-h:respond",
        satellite_execution_id="sat-h",
    )
    old = (
        datetime.now(UTC) - timedelta(seconds=max_open_leg_s(1800.0) + 10)
    ).isoformat()
    conn = _db_path()
    db = sqlite3.connect(conn)
    try:
        db.execute(
            "UPDATE cdp_inflight_leg SET admitted_at=? WHERE execution_id=?",
            (old, "root-h:respond"),
        )
        db.commit()
    finally:
        db.close()

    await reconcile.reconcile_cdp_inflight_legs()

    deliver.assert_not_called()
    leg = read_inflight_leg("root-h:respond")
    assert leg is not None
    assert leg.abandoned is True


@pytest.mark.asyncio
async def test_pre_migration_row_without_owner_column_still_delivers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row written before the owner column still reconciles and delivers.

    Every in-flight CDP generate at restart is such a row.
    """
    path = _db_path()
    conn = sqlite3.connect(path)
    try:
        conn.execute("DROP TABLE cdp_inflight_leg")
        conn.executescript(_OLD_LEDGER_DDL)
        conn.execute(
            "INSERT INTO cdp_inflight_leg "
            "(execution_id, request_id, satellite_execution_id, thread_id, "
            "pointer_turn, caller_agent, prompt_uri, model_id, max_wall_s, "
            "admitted_at, proof_emitted, delivered, abandoned, purpose) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, NULL)",
            (
                "exec-premig",
                "req-premig",
                "sat-premig",
                "5583",
                1,
                "dispatch",
                "cortex://p.md",
                "cdp/opus-5",
                1800.0,
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cdp_inflight_leg)")}
        assert "owner" not in cols
    finally:
        conn.close()

    deliver = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        deliver,
    )
    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", lambda *_a, **_k: None)
    monkeypatch.setattr(reconcile, "terminal_event_exists", lambda _eid: False)
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(return_value=_proof_snapshot()),
    )

    await reconcile.reconcile_cdp_inflight_legs()

    deliver.assert_awaited()
    leg = read_inflight_leg("exec-premig")
    assert leg is not None
    assert leg.owner is None
    assert leg.proof_emitted is True
    assert leg.delivered is True
