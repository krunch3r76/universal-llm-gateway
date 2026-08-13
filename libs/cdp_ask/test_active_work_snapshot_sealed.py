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
from cdp_ask.lane_admission import (
    ADVISOR_RESERVE,
    admission_regime,
    count_by_purpose_class,
    effective_abs_hard,
)

pytestmark = pytest.mark.offline

# Frozen from post-6885 wire shape (authority siblings) — scope keys additive.
_QUALIFIED_FIELD_GOLDEN: dict[str, Any] = {
    "running_count_authority": "recorded",
    "admission_count_authority": "recorded",
    "live_cse_count_authority": "observed",
    "registry_capacity_count_authority": "recorded",
    "effective_count_authority": "max(recorded, observed)",
}


def _capacity(
    *,
    busy: bool,
    running_count: int,
    execution_ids: list[str],
    rows: list[dict[str, object]] | None = None,
    live_cse_count: int = 0,
    registry_capacity_count: int = 0,
) -> dict[str, Any]:
    admission_count = running_count
    effective = max(running_count, live_cse_count)
    rows_list = list(rows or [])
    seat_count, other_count = count_by_purpose_class(rows_list)
    regime = admission_regime(seat_count)
    abs_hard_effective = effective_abs_hard(seat_count)
    return {
        "busy": busy,
        "running_count": running_count,
        "running_count_scope": "cdp_ask execution store, pending/running streams",
        "running_count_authority": "recorded",
        "admission_count": admission_count,
        "admission_count_scope": "running/stream admissions, this host (soft=2 hard=3)",
        "admission_count_authority": "recorded",
        "live_cse_count": live_cse_count,
        "live_cse_count_scope": "open CSE attachments (Chrome pages), this host",
        "live_cse_count_authority": "observed",
        "registry_capacity_count": registry_capacity_count,
        "registry_capacity_count_scope": (
            "active registry Chrome hosts (ports/profiles), this host"
        ),
        "registry_capacity_count_authority": "recorded",
        "effective_count": effective,
        "effective_count_scope": (
            "restart-drain aggregate max(running_count, live_cse_count); NOT admission"
        ),
        "effective_count_authority": "max(recorded, observed)",
        "execution_ids": execution_ids,
        "rows": rows or [],
        "soft_limit": LANE_SOFT_LIMIT,
        "hard_limit": LANE_HARD_LIMIT,
        "free_slots": max(0, abs_hard_effective - admission_count),
        "at_soft_limit": admission_count >= LANE_SOFT_LIMIT,
        "at_hard_limit": admission_count >= abs_hard_effective,
        "seat_count": seat_count,
        "other_count": other_count,
        "advisor_reserve": ADVISOR_RESERVE,
        "admission_regime": regime,
        "effective_abs_hard": abs_hard_effective,
    }


@pytest.fixture(autouse=True)
def _no_registry_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.count_capacity_lanes",
        lambda: 0,
    )


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
                "cdp_url": None,
                "chat_url": None,
                "source": None,
                "parent_thread": None,
                "mission_kind": None,
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
    decl.plain(
        "busy",
        reason="derived boolean: running_count > 0 or live_cse_count > 0",
    )
    decl.plain("soft_limit", reason="configured stream admission constant")
    decl.plain("hard_limit", reason="configured stream admission constant")
    decl.plain(
        "free_slots",
        reason="derived: effective_abs_hard - admission_count",
    )
    decl.plain("at_soft_limit", reason="derived: admission_count >= soft_limit")
    decl.plain(
        "at_hard_limit",
        reason="derived: admission_count >= effective_abs_hard",
    )
    decl.plain("seat_count", reason="derived: pending/running seat-purpose rows")
    decl.plain("other_count", reason="derived: pending/running non-seat rows")
    decl.plain("advisor_reserve", reason="configured reserved advisor slot count")
    decl.plain(
        "admission_regime",
        reason="additive when seat_count > hard_limit - reserve else carved",
    )
    decl.plain(
        "effective_abs_hard",
        reason="regime-aware absolute stream ceiling",
    )
    with pytest.raises(UnqualifiedScalarError, match="injected"):
        seal(snap, decl)


def test_live_cse_count_none_when_unobserved() -> None:
    from admission_common.qualified_scalar import AuthorityClass, QualifiedScalar

    scalar = QualifiedScalar(
        value=None,
        scope="open CSE attachments (Chrome pages), this host",
        authority=AuthorityClass.OBSERVED,
    )
    emitted = scalar.emit("live_cse_count")
    assert emitted["live_cse_count"] is None
    assert emitted["live_cse_count_authority"] == "observed"
