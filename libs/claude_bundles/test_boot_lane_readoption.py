"""Unit tests for boot lane re-adoption planner (arc 7119)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_bundles.boot_lane_readoption import (
    apply_boot_readoption_plan,
    plan_boot_lane_readoption,
    rehearsal_boot_readoption_plan,
)
from claude_bundles.cdp_lane import profile_for
from claude_bundles.cdp_orphans import LivePort


def _row(
    *,
    status: str = "active",
    port: int = 9229,
    profile_suffix: str = "reg-abc12345",
    chat_url: str | None = None,
) -> dict:
    profile = profile_for(profile_suffix)
    out = {
        "registration_id": "rid-1",
        "status": status,
        "port": port,
        "profile_suffix": profile_suffix,
        "profile": str(profile),
    }
    if chat_url:
        out["chat_url"] = chat_url
    return out


def _live(
    port: int = 9229,
    *,
    profile_suffix: str = "reg-abc12345",
    page_urls: tuple[str, ...] = (),
    has_live_cse: bool = False,
) -> LivePort:
    profile = profile_for(profile_suffix)
    return LivePort(
        port=port,
        profile=profile,
        page_urls=page_urls,
        has_live_cse=has_live_cse,
    )


def _plan(**kwargs: object):
    defaults = {
        "active_rows": {},
        "live_ports": [],
        "running_registration_ids": frozenset(),
        "wake_debt": frozenset(),
        "is_listening": lambda _p: False,
    }
    defaults.update(kwargs)
    return plan_boot_lane_readoption(**defaults)  # type: ignore[arg-type]


def test_a1_active_with_running_refuses() -> None:
    rows = {"live-rid": _row(status="active")}
    plan = _plan(active_rows=rows, running_registration_ids=frozenset({"live-rid"}))
    assert len(plan.would_adopt) == 0
    assert len(plan.would_orphan) == 0
    assert any(
        item.get("registration_id") == "live-rid"
        and item.get("reason") == "already_live_execution"
        for item in plan.would_refuse
    )


def test_a2_a3_active_live_host_adopts() -> None:
    rows = {"adopt-rid": _row(status="active", port=9230)}
    live = [_live(port=9230)]
    plan = _plan(active_rows=rows, live_ports=live)
    assert len(plan.would_adopt) == 1
    assert plan.would_adopt[0]["registration_id"] == "adopt-rid"
    assert plan.would_adopt[0]["reason"] == "live_host_match"


def test_a4_tcp_only_orphans() -> None:
    rows = {"tcp-rid": _row(status="active", port=9231)}
    plan = _plan(
        active_rows=rows,
        live_ports=[],
        is_listening=lambda p: p == 9231,
    )
    assert len(plan.would_orphan) == 1
    assert plan.would_orphan[0]["reason"] == "cdp_unresponsive"


def test_a5_wake_debt_dead_port_refuses() -> None:
    rows = {"debt-rid": _row(status="active", port=9232)}
    plan = _plan(
        active_rows=rows,
        wake_debt=frozenset({"debt-rid"}),
    )
    assert any(
        item.get("registration_id") == "debt-rid"
        and item.get("reason") == "wake_debt_no_host"
        for item in plan.would_refuse
    )


def test_a6_dead_port_orphans() -> None:
    rows = {"dead-rid": _row(status="active", port=9233)}
    plan = _plan(active_rows=rows)
    assert len(plan.would_orphan) == 1
    assert plan.would_orphan[0]["registration_id"] == "dead-rid"


def test_a7_profile_mismatch_orphans() -> None:
    rows = {"mis-rid": _row(status="active", port=9234, profile_suffix="reg-aaa11111")}
    live = [_live(port=9234, profile_suffix="reg-bbb22222")]
    plan = _plan(active_rows=rows, live_ports=live)
    assert len(plan.would_orphan) == 1
    assert plan.would_orphan[0]["reason"] == "profile_mismatch"


def test_a8_invalid_port_orphans() -> None:
    row = _row(status="active")
    row.pop("port")
    rows = {"bad-rid": row}
    plan = _plan(active_rows=rows)
    assert len(plan.would_orphan) == 1
    assert plan.would_orphan[0]["reason"] == "invalid_port"


def test_o1_orphaned_alive_live_adopts() -> None:
    rows = {"rev-rid": _row(status="orphaned_alive", port=9235)}
    live = [_live(port=9235)]
    plan = _plan(active_rows=rows, live_ports=live)
    assert len(plan.would_adopt) == 1
    assert plan.would_adopt[0]["prior_status"] == "orphaned_alive"


def test_o2_orphaned_alive_dead_refuses() -> None:
    rows = {"stay-rid": _row(status="orphaned_alive", port=9236)}
    plan = _plan(active_rows=rows)
    assert any(
        item.get("registration_id") == "stay-rid"
        and item.get("reason") == "orphan_no_live_match"
        for item in plan.would_refuse
    )


@pytest.mark.parametrize(
    "status",
    ["retained", "released", "orphaned_retry"],
)
def test_r1_r3_non_candidate_refuses(status: str) -> None:
    rows = {"hold-rid": _row(status=status, port=9237)}
    live = [_live(port=9237)]
    plan = _plan(active_rows=rows, live_ports=live)
    assert len(plan.would_adopt) == 0
    assert len(plan.would_orphan) == 0
    assert any(item.get("registration_id") == "hold-rid" for item in plan.would_refuse)


def test_r4_allocating_orphans_without_debt() -> None:
    rows = {"alloc-rid": _row(status="allocating", port=9238)}
    plan = _plan(active_rows=rows)
    assert len(plan.would_orphan) == 1
    assert plan.would_orphan[0]["reason"] == "incomplete_registration"


def test_r4_allocating_wake_debt_refuses() -> None:
    rows = {"alloc-debt": _row(status="allocating", port=9239)}
    plan = _plan(active_rows=rows, wake_debt=frozenset({"alloc-debt"}))
    assert any(
        item.get("registration_id") == "alloc-debt"
        and item.get("reason") == "allocating_wake_debt"
        for item in plan.would_refuse
    )


def test_p1_unregistered_cse_port_refuses() -> None:
    cse_url = "https://claude.ai/cowork/cse_test123"
    live = [
        _live(
            port=9240,
            page_urls=(cse_url,),
            has_live_cse=True,
        )
    ]
    plan = _plan(active_rows={}, live_ports=live)
    assert any(item.get("reason") == "unregistered_orphan_port" for item in plan.would_refuse)


def test_p2_primary_profile_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    from claude_bundles import cdp_lane

    primary = cdp_lane.PRIMARY_PROFILE
    live = [
        LivePort(
            port=9222,
            profile=primary,
            page_urls=(),
            has_live_cse=False,
        )
    ]
    plan = _plan(active_rows={}, live_ports=live)
    assert any(item.get("reason") == "primary_profile" for item in plan.would_refuse)


def test_p3_unresolved_profile_refuses() -> None:
    live = [LivePort(port=9241, profile=None, page_urls=(), has_live_cse=False)]
    plan = _plan(active_rows={}, live_ports=live)
    assert any(item.get("reason") == "profile_unresolved" for item in plan.would_refuse)


def test_cse_affinity_bound_present() -> None:
    cse = "https://claude.ai/cowork/cse_abc/"
    rows = {"aff-rid": _row(status="active", port=9242, chat_url=cse)}
    live = [_live(port=9242, page_urls=(cse,), has_live_cse=True)]
    plan = _plan(active_rows=rows, live_ports=live)
    assert plan.would_adopt[0]["cse_affinity"] == "bound_present"


def test_apply_plan_skips_refuse() -> None:
    plan = _plan(
        active_rows={"orph-rid": _row(status="active", port=9243)},
    )
    adopt = MagicMock()
    orphan = MagicMock()
    _, orphaned = apply_boot_readoption_plan(plan, adopt_fn=adopt, orphan_fn=orphan)
    assert orphaned == ["orph-rid"]
    adopt.assert_not_called()
    orphan.assert_called_once_with("orph-rid")


def test_rehearsal_does_not_mutate_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    writes: list[str] = []
    monkeypatch.setattr(
        "claude_bundles.boot_lane_readoption.gather_rehearsal_inputs",
        lambda **_: (
            {"rid-x": _row(status="active", port=9244)},
            [_live(port=9244)],
            set(),
            lambda _r: False,
        ),
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.deregister_lane",
        lambda *_a, **_k: writes.append("deregister"),
    )
    monkeypatch.setattr(
        "claude_bundles.boot_lane_readoption.boot_adopt_lane",
        lambda *_a, **_k: writes.append("adopt"),
    )
    plan = rehearsal_boot_readoption_plan(assume_empty_store=True)
    assert len(plan.would_adopt) == 1
    assert writes == []


def test_thin_execution_record_not_created_by_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thin ExecutionRecord default OFF — apply must not touch ExecutionStore."""
    from cdp_ask.execution_store import ExecutionStore

    store = ExecutionStore(reaper_interval_s=9999.0)
    plan = _plan(active_rows={"orph-rid": _row(status="active", port=9245)})
    monkeypatch.setattr(
        "claude_bundles.boot_lane_readoption.boot_adopt_lane",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.deregister_lane",
        lambda *_a, **_k: None,
    )
    apply_boot_readoption_plan(plan)
    assert store._records == {}
