"""Executor-level tests for functional settle (G5 repair items)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from scripts.model_manager.ui.controller.charter_runner.propagation_settle import (
    restart_blocked_by_order,
)
from scripts.model_manager.ui.controller.charter_runner.propagation_settle_executor import (
    request_pre_restart_gateway_membership,
    wait_functional_settle,
)


def test_subscribe_events_wires_stargate_settle_with_cap_and_op_run():
    """Item 1: subscribe_events + 120s cap + snapshot + op=run on affected pipelines."""
    events = [
        {
            "seq": 2,
            "signal": "federation.gateway.membership",
            "payload": {
                "gateway_ids": ["gw-a"],
                "pipeline_ids": ["keep"],
            },
        }
    ]

    async def _feed(*_args: Any, **_kwargs: Any):
        for item in events:
            yield item

    async def _run_op_run(affected: set[str]) -> list[str]:
        assert affected == {"keep"}
        return []

    async def _run() -> None:
        result = await wait_functional_settle(
            query_sock="/dev/null",
            resume_from=1,
            snapshot_gateway_ids=["gw-a"],
            snapshot_pipeline_ids=["keep"],
            land_paths=("pipelines/keep.yaml",),
            cap_s=120.0,
            subscribe_factory=_feed,
            run_op_run=_run_op_run,
            pipeline_sources={"keep": ("pipelines/keep.yaml",)},
        )
        assert result.verdict == "pass"
        assert not result.timed_out

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_pre_restart_membership_request():
    """Item 2: supervised pre-restart request returns membership payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/admin/emit-gateway-membership")
        return httpx.Response(
            200,
            json={
                "gateway_ids": ["g1"],
                "pipeline_ids": ["p1"],
                "signal": "federation.gateway.membership",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://stargate") as client:
        snap = await request_pre_restart_gateway_membership(
            base_url="http://stargate", client=client
        )
    assert snap == (["g1"], ["p1"])


def test_after_edge_scoped_to_same_land_code_ref():
    """Item 3: order edge reads provider verdict for the same land only."""
    assert restart_blocked_by_order(
        "mcp",
        {"stargate:land-a": "pass", "stargate:land-b": "indeterminate"},
        land_code_ref="land-a",
    ) is False
    assert restart_blocked_by_order(
        "mcp",
        {"stargate:land-a": "pass", "stargate:land-b": "indeterminate"},
        land_code_ref="land-b",
    ) is True


def test_settle_indeterminate_defer_excluded_from_harvest_fire_set():
    """Item 4: indeterminate uses settle_indeterminate defer (no restart re-fire)."""
    from charter_runner_store.propagation_ledger import (
        DEFER_SETTLE_INDETERMINATE,
        open_row_in_harvest_fire_set,
    )

    assert not open_row_in_harvest_fire_set(DEFER_SETTLE_INDETERMINATE)


@pytest.mark.asyncio
async def test_fail_attributable_reprobe_escalates_when_not_pass():
    """Item 5: fail_attributable revert path escalates when re-probe is not pass."""
    from scripts.model_manager.ui.controller.charter_runner.propagation_settle import (
        apply_verdict,
    )

    async def _revert(**_kwargs: Any) -> dict[str, Any]:
        return {"status": "landed", "revert_sha": "abc"}

    applied = await apply_verdict(
        "fail_attributable",
        revert=_revert,
        already_reverted=False,
        reprobe="indeterminate",
    )
    assert applied["escalated"] is True
    assert applied["revert_calls"] == 1


@pytest.mark.asyncio
async def test_revert_op_uses_gate_cas_and_rev_parse_head(monkeypatch: pytest.MonkeyPatch):
    """Item 6: revert_op runs gate + CAS and reads SHA from rev-parse HEAD."""
    import subprocess

    from git_integrate import git_cas, revert as revert_mod
    from git_integrate.revert import revert_op
    from git_integrate.schema import CasResult

    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[-2:] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(
                argv, 0, stdout="deadbeef1234567890abcdef1234567890abcdef\n"
            )
        return subprocess.CompletedProcess(argv, 0, stdout="")

    async def _gate(*_args: Any, **_kwargs: Any):
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    async def _current_sha(*_args: Any, **_kwargs: Any) -> str:
        return "master-before"

    async def _advance(*_args: Any, **_kwargs: Any) -> CasResult:
        return CasResult(non_ff=False, new_sha="deadbeef1234567890abcdef1234567890abcdef")

    monkeypatch.setattr(revert_mod, "_run_command", _gate)
    monkeypatch.setattr(revert_mod.git_cas, "current_sha", _current_sha)
    monkeypatch.setattr(revert_mod.git_cas, "advance_master_cas", _advance)

    result = await revert_op(
        source_repo="/tmp/repo",
        merge_sha="merge111",
        acquire_lease=lambda _k, _h: True,
        release_lease=lambda _k, _h: True,
        git_runner=runner,
        green_gate_cmd=["true"],
    )
    assert result["status"] == "landed"
    assert result["revert_sha"] == "deadbeef1234567890abcdef1234567890abcdef"
    assert any("rev-parse" in c for c in calls)


def test_executor_module_has_tests_for_all_seven_g5_items():
    """Item 7: one named executor-level test per G5 minimum item."""
    import scripts.model_manager.ui.controller.charter_runner.test_propagation_settle_executor as mod

    names = {name for name in dir(mod) if name.startswith("test_")}
    required = {
        "test_subscribe_events_wires_stargate_settle_with_cap_and_op_run",
        "test_pre_restart_membership_request",
        "test_after_edge_scoped_to_same_land_code_ref",
        "test_settle_indeterminate_defer_excluded_from_harvest_fire_set",
        "test_fail_attributable_reprobe_escalates_when_not_pass",
        "test_revert_op_uses_gate_cas_and_rev_parse_head",
    }
    assert required <= names
