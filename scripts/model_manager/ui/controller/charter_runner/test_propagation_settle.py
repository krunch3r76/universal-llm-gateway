"""Order edge, one revert, and GIW ready-join attribution."""

import pytest
from implement_admission.propagation_row import ORDER_AFTER, PropagationRow

from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
    proof_matches,
)
from scripts.model_manager.ui.controller.charter_runner.propagation_settle import (
    apply_verdict,
    giw_ready_join_verdict,
    restart_blocked_by_order,
    should_call_revert,
)


def test_mcp_blocked_until_stargate_pass():
    assert ORDER_AFTER["mcp"] == ("stargate",)
    assert restart_blocked_by_order("mcp", {})
    assert restart_blocked_by_order("mcp", {"stargate": "indeterminate"})
    assert restart_blocked_by_order("mcp", {"stargate": "fail_attributable"})
    assert not restart_blocked_by_order("mcp", {"stargate": "pass"})
    assert restart_blocked_by_order(
        "mcp",
        {"stargate:other-land": "indeterminate"},
        land_code_ref="land-a",
    )
    assert not restart_blocked_by_order(
        "mcp",
        {"stargate:other-land": "indeterminate", "stargate:land-a": "pass"},
        land_code_ref="land-a",
    )


def test_indeterminate_alone_does_not_revert():
    assert not should_call_revert({"stargate": "indeterminate"})
    assert should_call_revert(
        {"stargate": "fail_attributable", "mcp": "indeterminate"}
    )


@pytest.mark.asyncio
async def test_reprobe_not_pass_does_not_revert_twice():
    calls = {"n": 0}

    async def revert(**_kwargs):
        calls["n"] += 1
        return {"status": "landed", "worker_http": False}

    first = await apply_verdict(
        "fail_attributable",
        revert=revert,
        already_reverted=False,
        reprobe="indeterminate",
    )
    second = await apply_verdict(
        "indeterminate",
        revert=revert,
        already_reverted=True,
        reprobe="fail_attributable",
    )
    assert first["revert_calls"] == 1
    assert first["escalated"] is True
    assert second["revert_calls"] == 0
    assert calls["n"] == 1


def test_giw_ready_join_timeout_is_fail_attributable():
    assert giw_ready_join_verdict("timeout") == "fail_attributable"
    assert giw_ready_join_verdict("probe_down") == "fail_attributable"
    assert giw_ready_join_verdict("ready") == "pass"


def test_functional_settle_does_not_close_on_pid_proof(monkeypatch):
    row = PropagationRow(
        service="stargate",
        code_ref="abc",
        proof_class="functional_settle",
        safe_window="harvest",
        proof="functional_settle obligation",
    )

    class Projection:
        proof_class = "functional_settle"
        service = "stargate"
        proof = row.proof
        row_id = "row"
        code_ref = "abc"
        safe_window = "harvest"
        proof_class_requested = "functional_settle"
        allow_self_preempt = True
        force = False

    def boom(*_args, **_kwargs):
        raise AssertionError("proof_observed must not close functional_settle")

    monkeypatch.setattr(
        "services.git_integration_worker.relay.propagation_probe.proof_observed",
        boom,
    )
    assert proof_matches(Projection(), {"pid": 2}, before={"pid": 1}) is False
