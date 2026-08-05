"""Wire-inertness and seal-gate tests for sealed active_work_snapshot."""

from __future__ import annotations

from typing import Any

import pytest
from admission_common.qualified_scalar import UnqualifiedScalarError, seal

from cdp_ask.execution_store import (
    LANE_HARD_LIMIT,
    LANE_SOFT_LIMIT,
    ExecutionStore,
)

pytestmark = pytest.mark.offline

# Frozen from pre-seal wire shape (authority siblings) — scope keys are additive only.
_QUALIFIED_FIELD_GOLDEN: dict[str, Any] = {
    "running_count_authority": "recorded",
    "live_cse_count_authority": "observed",
    "effective_count_authority": "max(recorded, observed)",
}


def _capacity(
    *,
    busy: bool,
    running_count: int,
    execution_ids: list[str],
    rows: list[dict[str, object]] | None = None,
    live_cse_count: int = 0,
) -> dict[str, Any]:
    effective = max(running_count, live_cse_count)
    return {
        "busy": busy,
        "running_count": running_count,
        "running_count_scope": "cdp_ask execution store, pending/running records",
        "running_count_authority": "recorded",
        "live_cse_count": live_cse_count,
        "live_cse_count_scope": "browser CSE lanes, this host",
        "live_cse_count_authority": "observed",
        "effective_count": effective,
        "effective_count_scope": "max(running_count, live_cse_count), this host",
        "effective_count_authority": "max(recorded, observed)",
        "execution_ids": execution_ids,
        "rows": rows or [],
        "soft_limit": LANE_SOFT_LIMIT,
        "hard_limit": LANE_HARD_LIMIT,
        "free_slots": max(0, LANE_HARD_LIMIT - effective),
        "at_soft_limit": effective >= LANE_SOFT_LIMIT,
        "at_hard_limit": effective >= LANE_HARD_LIMIT,
    }


@pytest.mark.asyncio
async def test_sealed_snapshot_wire_identical_for_qualified_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )
    store = ExecutionStore()
    record = await store.create(holder="test", purpose="ask")
    snap = await store.active_work_snapshot()
    for key, expected in _QUALIFIED_FIELD_GOLDEN.items():
        assert snap[key] == expected, f"{key}: {snap[key]!r} != {expected!r}"
    assert snap["running_count"] == 1
    assert snap["live_cse_count"] == 0
    assert snap["effective_count"] == 1
    assert snap == _capacity(
        busy=True,
        running_count=1,
        execution_ids=[record.execution_id],
        rows=[
            {
                "execution_id": record.execution_id,
                "registration_id": None,
                "holder": "test",
                "purpose": "ask",
                "status": "pending",
            }
        ],
    )


@pytest.mark.asyncio
async def test_seal_raises_on_injected_undeclared_bare_numeric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )
    store = ExecutionStore()
    snap = await store.active_work_snapshot()
    snap["injected"] = 42
    from admission_common.qualified_scalar import SurfaceDecl

    decl = SurfaceDecl("active_work_snapshot")
    decl.plain("busy", reason="derived boolean: effective_count > 0")
    decl.plain("soft_limit", reason="configured lane admission constant")
    decl.plain("hard_limit", reason="configured lane admission constant")
    decl.plain("free_slots", reason="derived: hard_limit - effective_count")
    decl.plain("at_soft_limit", reason="derived: effective >= soft_limit")
    decl.plain("at_hard_limit", reason="derived: effective >= hard_limit")
    with pytest.raises(UnqualifiedScalarError, match="injected"):
        seal(snap, decl)


def test_live_cse_count_none_when_unobserved() -> None:
    from admission_common.qualified_scalar import AuthorityClass, QualifiedScalar

    scalar = QualifiedScalar(
        value=None,
        scope="browser CSE lanes, this host",
        authority=AuthorityClass.OBSERVED,
    )
    emitted = scalar.emit("live_cse_count")
    assert emitted["live_cse_count"] is None
    assert emitted["live_cse_count_authority"] == "observed"
