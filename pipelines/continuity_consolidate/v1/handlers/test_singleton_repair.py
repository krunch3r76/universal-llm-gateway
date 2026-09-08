"""F1 singleton repair — offline falsifiers for burst-replay debris."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pipelines.continuity_consolidate.v1.handlers._cortex import (
    MISSION_PREFIX,
    RESUME_PREFIX,
    SEEDED_BY,
    WATERMARK_PREFIX,
)
from pipelines.continuity_consolidate.v1.handlers._plan import (
    WritePlan,
    active_pipeline_ids_by_prefix,
    cap_distill_claims,
    singleton_active_counts,
)

pytestmark = pytest.mark.offline


def _row(assertion_id: int, claim: str, *, superseded_by: int | None = None) -> dict:
    return {
        "id": assertion_id,
        "claim": claim,
        "seeded_by": SEEDED_BY,
        "superseded_by": superseded_by,
    }


def test_singleton_active_counts_flags_burst_duplicates():
    rows = [
        _row(10, f"{WATERMARK_PREFIX}10303#1 at t1"),
        _row(11, f"{WATERMARK_PREFIX}10303#2 at t2"),
        _row(20, f"{MISSION_PREFIX}fold mission"),
        _row(30, f"{RESUME_PREFIX}settled=x | live=y | next=z"),
    ]
    counts = singleton_active_counts(rows)
    assert counts == {"watermark": 2, "mission": 1, "resume": 1}


def test_active_pipeline_ids_ignores_superseded():
    rows = [
        _row(10, f"{WATERMARK_PREFIX}old", superseded_by=12),
        _row(12, f"{WATERMARK_PREFIX}new"),
    ]
    assert active_pipeline_ids_by_prefix(rows, WATERMARK_PREFIX) == [12]


@pytest.mark.asyncio
async def test_repair_chains_all_but_newest_per_prefix():
    client = AsyncMock()
    rows = [
        _row(10, f"{WATERMARK_PREFIX}10303#100 at t1"),
        _row(11, f"{WATERMARK_PREFIX}10303#101 at t2"),
        _row(12, f"{WATERMARK_PREFIX}10303#102 at t3"),
        _row(20, f"{MISSION_PREFIX}mission a"),
        _row(21, f"{MISSION_PREFIX}mission b"),
    ]
    plan = WritePlan(dry_run=False)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "pipelines.continuity_consolidate.v1.handlers._plan.dispatch",
            AsyncMock(return_value={}),
        )
        chained = await plan.repair_pipeline_singletons(client, rows)
    assert chained == {"watermark": 2, "mission": 1, "resume": 0}
    chain_entries = [e for e in plan.entries if e.get("tool") == "assertion_update"]
    assert len(chain_entries) == 3
    stale_ids = {e["arguments"]["assertion_id"] for e in chain_entries}
    assert stale_ids == {10, 11, 20}
    keep_targets = {e["arguments"]["superseded_by"] for e in chain_entries}
    assert keep_targets == {12, 21}


@pytest.mark.asyncio
async def test_write_singleton_chains_concurrent_sibling_not_only_older():
    client = AsyncMock()
    plan = WritePlan(dry_run=False)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "pipelines.continuity_consolidate.v1.handlers._plan.dispatch",
            AsyncMock(
                side_effect=lambda _client, tool, _args: {"id": 99}
                if tool == "assert"
                else {}
            ),
        )
        prior_ids = [50, 51, 52]
        await plan.write_singleton(
            client,
            "watermark",
            hub_id="document:10223-continuity",
            claim=f"{WATERMARK_PREFIX}10303#200 at t",
            quoted=True,
            evidence="test",
            evidence_uris=["agent-bus:10303#200"],
            prior_ids=prior_ids,
        )
    chain_entries = [e for e in plan.entries if e.get("tool") == "assertion_update"]
    assert {e["arguments"]["assertion_id"] for e in chain_entries} == {50, 51, 52}
    assert all(e["arguments"]["superseded_by"] == 99 for e in chain_entries)


def test_cap_distill_claims_drops_oldest_per_kind():
    claims = [
        {"kind": "closed", "claim": f"c{i}"} for i in range(10)
    ] + [{"kind": "open", "claim": f"o{i}"} for i in range(6)]
    capped, dropped = cap_distill_claims(claims)
    closed = [c for c in capped if c["kind"] == "closed"]
    open_ = [c for c in capped if c["kind"] == "open"]
    assert len(closed) == 8
    assert closed[0]["claim"] == "c2"
    assert closed[-1]["claim"] == "c9"
    assert len(open_) == 4
    assert dropped == 4
