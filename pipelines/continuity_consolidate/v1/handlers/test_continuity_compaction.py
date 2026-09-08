"""S4-A claim-growth compaction — per-kind distill caps + singleton stub."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pipelines.continuity_consolidate.v1.handlers._cortex import (
    SEEDED_BY,
    WATERMARK_PREFIX,
)
from pipelines.continuity_consolidate.v1.handlers._plan import (
    CLAIM_KIND_CAPS,
    WritePlan,
    cap_distill_claims,
    singleton_active_counts,
)

pytestmark = pytest.mark.offline


def _claim(kind: str, text: str) -> dict[str, str]:
    return {"kind": kind, "claim": text}


def test_claim_kind_caps_match_s4a_table():
    assert CLAIM_KIND_CAPS == {
        "closed": 8,
        "open": 4,
        "decided": 4,
        "artifact": 4,
        "superseded": 2,
    }


def test_cap_distill_claims_drops_oldest_closed_past_8():
    claims = [_claim("closed", f"c{i}") for i in range(10)]
    capped, dropped = cap_distill_claims(claims)
    assert dropped == 2
    assert [c["claim"] for c in capped] == [f"c{i}" for i in range(2, 10)]


def test_cap_distill_claims_independent_kinds():
    claims = (
        [_claim("closed", f"c{i}") for i in range(9)]
        + [_claim("open", f"o{i}") for i in range(5)]
        + [_claim("decided", f"d{i}") for i in range(4)]
        + [_claim("artifact", f"a{i}") for i in range(6)]
        + [_claim("superseded", f"s{i}") for i in range(3)]
    )
    capped, dropped = cap_distill_claims(claims)
    by_kind = {}
    for item in capped:
        by_kind.setdefault(item["kind"], []).append(item["claim"])
    assert len(by_kind["closed"]) == 8
    assert by_kind["closed"][0] == "c1"
    assert len(by_kind["open"]) == 4
    assert by_kind["open"][0] == "o1"
    assert by_kind["decided"] == [f"d{i}" for i in range(4)]
    assert by_kind["artifact"] == [f"a{i}" for i in range(2, 6)]
    assert by_kind["superseded"] == ["s1", "s2"]
    assert dropped == 1 + 1 + 0 + 2 + 1


def test_cap_distill_claims_preserves_interleaved_order():
    claims = [
        _claim("open", "o0"),
        _claim("closed", "c0"),
        _claim("open", "o1"),
        _claim("closed", "c1"),
    ]
    capped, dropped = cap_distill_claims(claims)
    assert dropped == 0
    assert [c["claim"] for c in capped] == ["o0", "c0", "o1", "c1"]


def test_cap_distill_claims_passthrough_unknown_kind():
    claims = [_claim("policy", "house rule")] + [
        _claim("closed", f"c{i}") for i in range(3)
    ]
    capped, dropped = cap_distill_claims(claims)
    assert dropped == 0
    assert capped[0]["kind"] == "policy"
    assert len(capped) == 4


def test_cap_distill_claims_ignores_non_dicts():
    claims = ["nope", _claim("open", "keep"), None]
    capped, dropped = cap_distill_claims(claims)
    assert dropped == 0
    assert capped == [_claim("open", "keep")]


@pytest.mark.asyncio
async def test_singleton_active_count_after_apply_plan():
    """After WritePlan.write_singleton, mock graph has exactly 1 active WATERMARK."""
    client = AsyncMock()
    priors = [
        {
            "id": 10,
            "claim": f"{WATERMARK_PREFIX}old",
            "seeded_by": SEEDED_BY,
            "superseded_by": None,
        },
        {
            "id": 11,
            "claim": f"{WATERMARK_PREFIX}mid",
            "seeded_by": SEEDED_BY,
            "superseded_by": None,
        },
    ]
    plan = WritePlan(dry_run=False)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "pipelines.continuity_consolidate.v1.handlers._plan.dispatch",
            AsyncMock(
                side_effect=lambda _client, tool, _args: (
                    {"id": 99} if tool == "assert" else {}
                )
            ),
        )
        await plan.write_singleton(
            client,
            "watermark",
            hub_id="document:10223-continuity",
            claim=f"{WATERMARK_PREFIX}10303#105 at t",
            quoted=True,
            evidence="test",
            evidence_uris=["agent-bus:10303#105"],
            prior_ids=[10, 11],
        )
    chained = {
        e["arguments"]["assertion_id"]: e["arguments"]["superseded_by"]
        for e in plan.entries
        if e.get("tool") == "assertion_update"
    }
    assert chained == {10: 99, 11: 99}
    mock_graph = [
        {**row, "superseded_by": chained.get(row["id"])} for row in priors
    ] + [
        {
            "id": 99,
            "claim": f"{WATERMARK_PREFIX}10303#105 at t",
            "seeded_by": SEEDED_BY,
            "superseded_by": None,
        }
    ]
    assert singleton_active_counts(mock_graph)["watermark"] == 1
