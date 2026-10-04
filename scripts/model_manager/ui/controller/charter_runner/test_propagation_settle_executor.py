"""Executor-level tests for functional settle (G5 repair items)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from scripts.model_manager.ui.controller.charter_runner.propagation_settle import (
    restart_blocked_by_order,
)
from scripts.model_manager.ui.controller.charter_runner.propagation_settle_executor import (
    request_pre_restart_gateway_membership,
    wait_functional_settle,
)


def test_subscribe_events_wires_stargate_settle_with_cap_and_op_run():
    """Live subscribe ends when the collector stops on a matching membership event."""

    async def _feed(*_args: Any, **_kwargs: Any):
        yield {
            "seq": 2,
            "signal": "federation.gateway.membership",
            "payload": {
                "gateway_ids": ["gw-a"],
                "pipeline_ids": ["keep"],
            },
        }
        await asyncio.Event().wait()

    async def _run_op_run(affected: set[str]) -> list[str]:
        assert set(affected) == {"keep"}
        return []

    async def _run() -> None:
        result = await asyncio.wait_for(
            wait_functional_settle(
                query_sock="/dev/null",
                resume_from=1,
                snapshot_gateway_ids=["gw-a"],
                snapshot_pipeline_ids=["keep"],
                land_paths=("pipelines/keep.yaml",),
                cap_s=120.0,
                subscribe_factory=_feed,
                run_op_run=_run_op_run,
                pipeline_sources={"keep": ("pipelines/keep.yaml",)},
            ),
            timeout=2.0,
        )
        assert result.verdict == "pass"
        assert not result.timed_out
        assert result.events_seen == 1

    asyncio.run(_run())


def test_partial_pipeline_membership_does_not_stop_the_wait():
    """Full gateway set with a short pipeline list keeps the wait open until every unaffected pipeline is present."""

    gateways = [f"g{i}" for i in range(8)]
    unaffected = [f"p{i:02d}" for i in range(24)]
    pipelines = unaffected + ["en-en-csc"]
    partial = unaffected[:12]

    async def _feed(*_args: Any, **_kwargs: Any):
        yield {
            "seq": 10,
            "signal": "federation.gateway.membership",
            "payload": {"gateway_ids": gateways, "pipeline_ids": partial},
        }
        yield {
            "seq": 20,
            "signal": "federation.gateway.membership",
            "payload": {"gateway_ids": gateways, "pipeline_ids": pipelines},
        }

    async def _run_op_run(_affected: set[str]) -> list[str]:
        return []

    async def _run() -> None:
        result = await asyncio.wait_for(
            wait_functional_settle(
                query_sock="/dev/null",
                resume_from=1,
                snapshot_gateway_ids=gateways,
                snapshot_pipeline_ids=pipelines,
                land_paths=("pipelines/en-en-csc.yaml",),
                cap_s=5.0,
                subscribe_factory=_feed,
                run_op_run=_run_op_run,
                pipeline_sources={"en-en-csc": ("pipelines/en-en-csc.yaml",)},
            ),
            timeout=2.0,
        )
        assert result.verdict == "pass"
        assert not result.timed_out
        assert result.events_seen == 2

    asyncio.run(_run())


def test_expected_absent_pipeline_does_not_block_readiness():
    """A deleted pipeline omitted from membership does not hold the wait once unaffected ids are present."""

    async def _feed(*_args: Any, **_kwargs: Any):
        yield {
            "seq": 4,
            "signal": "federation.gateway.membership",
            "payload": {"gateway_ids": ["gw-a"], "pipeline_ids": ["keep"]},
        }
        await asyncio.Event().wait()

    ran: list[set[str]] = []

    async def _run_op_run(affected: set[str]) -> list[str]:
        ran.append(set(affected))
        return []

    async def _run() -> None:
        result = await asyncio.wait_for(
            wait_functional_settle(
                query_sock="/dev/null",
                resume_from=1,
                snapshot_gateway_ids=["gw-a"],
                snapshot_pipeline_ids=["keep", "gone"],
                land_paths=("pipelines/gone.yaml",),
                land_deleted_paths=("pipelines/gone.yaml",),
                cap_s=5.0,
                subscribe_factory=_feed,
                run_op_run=_run_op_run,
                pipeline_sources={
                    "keep": ("pipelines/keep.yaml",),
                    "gone": ("pipelines/gone.yaml",),
                },
            ),
            timeout=2.0,
        )
        assert result.verdict == "pass"
        assert not result.timed_out
        assert result.events_seen == 1
        assert ran == []

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_pre_restart_membership_request():
    """Production path reads the latest membership event from Event Service."""
    captured: list[dict[str, Any]] = []

    def _query(body: dict[str, Any]) -> dict[str, Any]:
        captured.append(body)
        return {
            "rows": [
                {
                    "seq": 10,
                    "signal": "federation.gateway.membership",
                    "payload": {"gateway_ids": ["g-old"], "pipeline_ids": ["p-old"]},
                },
                {
                    "seq": 42,
                    "signal": "federation.gateway.membership",
                    "payload": {"gateway_ids": ["g1"], "pipeline_ids": ["p1"]},
                },
            ]
        }

    snap = await request_pre_restart_gateway_membership(query_fn=_query)
    assert snap == (["g1"], ["p1"])
    assert captured[0]["name"] == "signal-events"
    assert captured[0]["params"]["signal"] == "federation.gateway.membership"


def test_after_edge_mcp_only_land_not_blocked_without_provider_row():
    """No stargate row for this land ⇒ missing land key must not block mcp."""
    assert not restart_blocked_by_order("mcp", {}, land_code_ref="mcp-only-land")
    assert not restart_blocked_by_order(
        "mcp",
        {"stargate": "pass", "stargate:other": "pass"},
        land_code_ref="this-land",
    )


def test_after_edge_pending_land_scoped_verdict_blocks_mcp():
    """Open settling stargate row surfaces as pending and blocks the same land."""
    assert restart_blocked_by_order(
        "mcp",
        {"stargate:shared-land": "pending"},
        land_code_ref="shared-land",
    )


def test_closed_null_ancestor_does_not_block_descendant_mcp(tmp_path, monkeypatch):
    """Closed stargate row with no verdict must not defer a descendant mcp restart."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    from charter_runner_store.db import open_ledger_db
    from charter_runner_store.propagation_ledger import provider_settle_verdicts
    from universal_workspace import get_workspace_root

    ancestor = "a1225745f97b1cdd0b4467ac0c0e71d7c66cd28b"
    descendant = "e92cdf077253210a89296318de0a24d3946947c3"
    repo = get_workspace_root()
    conn = open_ledger_db()
    try:
        conn.execute(
            """
            INSERT INTO propagation_ledger (
              row_id, service, action, code_ref, safe_window, proof, proof_class,
              status, age_in_harvests, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'closed', 0, 1.0, 1.0)
            """,
            (
                "stargate-closed-null",
                "stargate",
                "sync_restart",
                ancestor,
                "harvest",
                "probe",
                "functional_settle",
            ),
        )
        conn.execute(
            """
            INSERT INTO propagation_ledger (
              row_id, service, action, code_ref, safe_window, proof, proof_class,
              status, age_in_harvests, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 0, 2.0, 2.0)
            """,
            (
                "mcp-descendant",
                "mcp",
                "sync_restart",
                descendant,
                "harvest",
                "probe",
                "process_live",
            ),
        )
        conn.commit()
        verdicts = provider_settle_verdicts(conn=conn)
    finally:
        conn.close()
    assert f"stargate:{ancestor}" not in verdicts
    assert not restart_blocked_by_order(
        "mcp", verdicts, land_code_ref=descendant, source_repo=repo
    )


def test_newer_stargate_pass_supersedes_older_non_pass():
    """A newer in-scope pass governs; an older non-pass does not deadlock mcp."""
    from universal_workspace import get_workspace_root

    ancestor = "a1225745f97b1cdd0b4467ac0c0e71d7c66cd28b"
    descendant = "e92cdf077253210a89296318de0a24d3946947c3"
    repo = get_workspace_root()
    assert not restart_blocked_by_order(
        "mcp",
        {
            f"stargate:{ancestor}": "fail_attributable",
            f"stargate:{descendant}": "pass",
        },
        land_code_ref=descendant,
        source_repo=repo,
    )


def test_after_edge_ancestor_stargate_pending_blocks_later_mcp_land():
    """B2: unsettled ancestor stargate land blocks mcp on a descendant-only land."""
    from universal_workspace import get_workspace_root

    ancestor = "a1225745f97b1cdd0b4467ac0c0e71d7c66cd28b"
    descendant = "e92cdf077253210a89296318de0a24d3946947c3"
    repo = get_workspace_root()
    verdicts = {f"stargate:{ancestor}": "pending"}
    assert restart_blocked_by_order(
        "mcp", verdicts, land_code_ref=descendant, source_repo=repo
    )
    assert not restart_blocked_by_order(
        "mcp", {}, land_code_ref=descendant, source_repo=repo
    )
    assert restart_blocked_by_order(
        "mcp",
        {f"stargate:{ancestor}": "indeterminate"},
        land_code_ref=descendant,
        source_repo=repo,
    )


def test_after_edge_scoped_to_same_land_code_ref():
    """Item 3: order edge reads provider verdict for the same land only."""
    assert (
        restart_blocked_by_order(
            "mcp",
            {"stargate:land-a": "pass", "stargate:land-b": "indeterminate"},
            land_code_ref="land-a",
        )
        is False
    )
    assert (
        restart_blocked_by_order(
            "mcp",
            {"stargate:land-a": "pass", "stargate:land-b": "indeterminate"},
            land_code_ref="land-b",
        )
        is True
    )


def test_settle_indeterminate_defer_excluded_from_harvest_fire_set():
    """Item 4: indeterminate uses settle_indeterminate defer (no restart re-fire)."""
    from charter_runner_store.propagation_ledger import (
        DEFER_SETTLE_INDETERMINATE,
        open_row_in_harvest_fire_set,
    )

    assert not open_row_in_harvest_fire_set(DEFER_SETTLE_INDETERMINATE)
    from charter_runner_store.propagation_ledger import DEFER_SETTLE_FAIL_ATTRIBUTABLE

    assert not open_row_in_harvest_fire_set(DEFER_SETTLE_FAIL_ATTRIBUTABLE)


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
async def test_revert_op_uses_gate_cas_and_rev_parse_head(
    monkeypatch: pytest.MonkeyPatch,
):
    """Item 6: revert_op runs gate + CAS and reads SHA from rev-parse HEAD."""
    import subprocess

    from git_integrate import revert as revert_mod
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
        return CasResult(
            non_ff=False, new_sha="deadbeef1234567890abcdef1234567890abcdef"
        )

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
    assert not any(c[1:4] == ["-C", "/tmp/repo", "revert"] for c in calls)
    assert result["revert_sha"] == "deadbeef1234567890abcdef1234567890abcdef"
    assert any("rev-parse" in c for c in calls)


@pytest.mark.asyncio
async def test_revert_conflict_aborts_and_does_not_count(
    monkeypatch: pytest.MonkeyPatch,
):
    """Conflict abort on the revert worktree. A revert that does not land counts 0."""
    import subprocess

    from git_integrate import revert as revert_mod
    from git_integrate.revert import revert_op

    from scripts.model_manager.ui.controller.charter_runner.propagation_settle import (
        apply_verdict,
    )

    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if "revert" in argv and "--abort" not in argv and "worktree" not in argv:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="conflict")
        return subprocess.CompletedProcess(argv, 0, stdout="")

    async def _current_sha(*_args: Any, **_kwargs: Any) -> str:
        return "master-before"

    monkeypatch.setattr(revert_mod.git_cas, "current_sha", _current_sha)

    result = await revert_op(
        source_repo="/tmp/repo",
        merge_sha="merge111",
        acquire_lease=lambda _k, _h: True,
        release_lease=lambda _k, _h: True,
        git_runner=runner,
        green_gate_cmd=["true"],
    )
    assert result["status"] == "indeterminate"
    assert result["reason"] == "revert_conflict"
    assert any("--abort" in c for c in calls)
    assert not any(c[1:4] == ["-C", "/tmp/repo", "revert"] for c in calls)

    async def _revert(**_kwargs: Any) -> dict[str, Any]:
        return result

    applied = await apply_verdict(
        "fail_attributable",
        revert=_revert,
        already_reverted=False,
    )
    assert applied["revert_calls"] == 0


@pytest.mark.asyncio
async def test_execute_functional_settle_missing_pipeline_fail_attributable_reverts(
    tmp_path, monkeypatch
):
    """B1: land pipeline YAML absent after restart → fail_attributable and one revert."""
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock, patch

    from implement_admission.propagation_row import PropagationRow
    from implement_admission.settle_pipeline_maps import FunctionalSettlePipelineMaps

    from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
        PropagationPlan,
        execute_propagation_plan,
        install_propagation_context,
    )

    land_sha = "e92cdf077253210a89296318de0a24d3946947c3"
    yaml_path = "pipelines/settle-missing-test.yaml"
    pipeline_id = "settle-missing-test"
    maps = FunctionalSettlePipelineMaps(
        pipeline_sources={pipeline_id: (yaml_path,)},
        step_type_modules={},
        pipeline_step_types={pipeline_id: ()},
    )

    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    from charter_runner_store.propagation_ledger import upsert_open_rows

    upsert_open_rows(
        [
            PropagationRow(
                service="stargate",
                code_ref=land_sha,
                proof_class="functional_settle",
            )
        ]
    )

    async def _feed(*_args: Any, **_kwargs: Any):
        yield {
            "seq": 2,
            "signal": "federation.gateway.membership",
            "payload": {"gateway_ids": ["gw-a"], "pipeline_ids": []},
        }
        await asyncio.Event().wait()

    revert_calls = {"n": 0}

    async def _revert_op(**_kwargs: Any) -> dict[str, Any]:
        revert_calls["n"] += 1
        return {"status": "landed", "revert_sha": "abc"}

    real_wait = wait_functional_settle
    captured_maps: list[dict[str, Any]] = []

    async def _wait_with_feed(**kwargs: Any):
        captured_maps.append(
            {
                "pipeline_sources": kwargs.get("pipeline_sources"),
                "land_paths": kwargs.get("land_paths"),
            }
        )
        kwargs.pop("subscribe_factory", None)
        run_op_run = kwargs.pop("run_op_run", None)
        if run_op_run is None:
            run_op_run = AsyncMock(return_value=[])
        return await real_wait(
            subscribe_factory=_feed,
            run_op_run=run_op_run,
            **kwargs,
        )

    cfg = MagicMock(source_repo=Path("/tmp/repo"))
    plan = PropagationPlan(rows=[], sync_restart_services=[])
    ctl = MagicMock()
    install_propagation_context(ctl, event_bus=None)

    with (
        patch(
            "scripts.model_manager.ui.controller.charter_runner.propagation_execute._fetch_drain_state",
            return_value={"active_ops": []},
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch.sync_restart_charter_harvest",
            new=AsyncMock(return_value={"status": "ok"}),
        ),
        patch("services.git_integration_worker.config.load_config", return_value=cfg),
        patch(
            "services.git_integration_worker.cursor_sdk_git_head.land_paths_from_merge_sha",
            return_value=(yaml_path,),
        ),
        patch(
            "implement_admission.settle_pipeline_maps.functional_settle_pipeline_maps",
            return_value=maps,
        ),
        patch(
            "scripts.model_manager.ui.controller.charter_runner.propagation_settle_executor.wait_functional_settle",
            side_effect=_wait_with_feed,
        ),
        patch("git_integrate.revert.revert_op", side_effect=_revert_op),
        patch(
            "scripts.model_manager.ui.controller.charter_runner.propagation_settle_executor.request_pre_restart_gateway_membership",
            new=AsyncMock(return_value=(["gw-a"], [pipeline_id])),
        ),
        patch(
            "scripts.model_manager.ui.controller.charter_runner.propagation_settle_executor.capture_event_resume_from",
            new=AsyncMock(return_value=1),
        ),
    ):
        results = await execute_propagation_plan(plan, root_id="root", window_index=1)

    assert captured_maps
    assert captured_maps[0]["pipeline_sources"] == maps.pipeline_sources
    assert yaml_path in captured_maps[0]["land_paths"]
    assert revert_calls["n"] == 1
    remaining = results["remaining"]
    assert any(r.get("verdict") == "fail_attributable" for r in remaining)


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
