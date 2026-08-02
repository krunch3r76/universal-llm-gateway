"""Unit tests for propagation_probe proof_class closure (a:27414 fix b)."""

from __future__ import annotations

from unittest.mock import patch

from implement_admission.propagation_row import PropagationRow

from services.git_integration_worker.cursor_auto.propagation_probe import (
    process_identity,
    proof_observed,
    strong_process_identity,
)

_SHA_A = "abc1230000000000000000000000000000000000"
_SHA_B = "deadbeef00000000000000000000000000000000"
_SHA_OLD = "old000000000000000000000000000000000000"
_SHA_STALE = "stale0000000000000000000000000000000000"


def _row(
    service: str,
    code_ref: str,
    *,
    proof_class: str = "process_live",
) -> PropagationRow:
    return PropagationRow(
        service=service,
        code_ref=code_ref,
        safe_window="standalone_ok",
        proof="test probe",
        proof_class=proof_class,
    )


def test_process_identity_prefers_pid() -> None:
    assert process_identity({"pid": 42, "process_age_s": 1.0}) == "pid:42"


def test_ac17p_proof_observed_reaches_deployment_identity_emit() -> None:
    """Pre-fix: TypeError on settle_not_before_monotonic kwarg blocked emit entirely."""
    row = _row("git_integration_worker", _SHA_A, proof_class="process_live")
    before = {"code_version": _SHA_OLD, "pid": 100}
    after = {"code_version": _SHA_A, "pid": 200}
    emit_path = (
        "services.git_integration_worker.cursor_sdk_boundary_deployment_identity"
        ".emit_deployment_identity_boundary"
    )
    with patch(emit_path, wraps=__import__(
        "services.git_integration_worker.cursor_sdk_boundary_deployment_identity",
        fromlist=["emit_deployment_identity_boundary"],
    ).emit_deployment_identity_boundary) as emit_mock:
        assert proof_observed(row, after, before=before) is True
        emit_mock.assert_called_once()
        emit_arg = emit_mock.call_args[0][0]
        assert emit_arg.expected_executor == "git_integration_worker"
        assert emit_arg.probed_surface == "git_integration_worker"
        assert emit_arg.landed_at_monotonic is None


def test_ac17p_proof_observed_maps_settle_not_before_to_landed_at_monotonic() -> None:
    """Post-restart propagation path must pass landed_at_monotonic, not settle_not_before."""
    import time

    row = _row("git_integration_worker", _SHA_A, proof_class="process_live")
    settle_not_before = time.monotonic() - 30.0
    after = {"code_version": _SHA_A, "uptime_s": 2.0, "pid": 526100}
    emit_path = (
        "services.git_integration_worker.cursor_sdk_boundary_deployment_identity"
        ".emit_deployment_identity_boundary"
    )
    with patch(emit_path) as emit_mock:
        assert proof_observed(
            row,
            after,
            settle_not_before_monotonic=settle_not_before,
        )
        emit_mock.assert_called_once()
        emit_arg = emit_mock.call_args[0][0]
        assert emit_arg.landed_at_monotonic == settle_not_before
        assert not hasattr(emit_arg, "settle_not_before_monotonic")


def test_proof_observed_process_live_without_before_is_false() -> None:
    row = _row("mcp", _SHA_A, proof_class="process_live")
    assert proof_observed(row, {"code_version": _SHA_A, "pid": 1}) is False


def test_proof_observed_process_live_identity_changed_and_version_match() -> None:
    row = _row("cortex_api", _SHA_A, proof_class="process_live")
    before = {"code_version": _SHA_OLD, "pid": 100}
    after = {"code_version": _SHA_A, "pid": 200}
    assert proof_observed(row, after, before=before) is True


def test_proof_observed_process_live_ancestor_passes() -> None:
    import subprocess

    from universal_workspace import get_workspace_root

    root = get_workspace_root()
    ancestor = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD~1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    row = _row("cortex_api", ancestor, proof_class="process_live")
    before = {"code_version": "old000", "pid": 100}
    after = {"code_version": head, "pid": 200}
    assert proof_observed(row, after, before=before) is True


def test_proof_observed_process_live_unrelated_ref_fails() -> None:
    row = _row("mcp", _SHA_A, proof_class="process_live")
    before = {"code_version": _SHA_A, "pid": 100}
    after = {"code_version": "0" * 40, "pid": 200}
    assert proof_observed(row, after, before=before) is False


def test_proof_observed_process_live_identity_unchanged_and_version_match() -> None:
    row = _row("cortex_api", _SHA_A, proof_class="process_live")
    before = {"code_version": _SHA_A, "pid": 100}
    after = {"code_version": _SHA_A, "pid": 100}
    assert proof_observed(row, after, before=before) is False


def test_proof_observed_process_live_version_mismatch() -> None:
    row = _row("mcp", _SHA_A, proof_class="process_live")
    before = {"code_version": _SHA_A, "pid": 100}
    after = {"code_version": _SHA_B, "pid": 200}
    assert proof_observed(row, after, before=before) is False


def test_proof_observed_process_live_missing_identity_fields() -> None:
    row = _row("mcp", _SHA_A, proof_class="process_live")
    before = {"code_version": _SHA_A}
    after = {"code_version": _SHA_A, "pid": 200}
    assert proof_observed(row, after, before=before) is False


def test_proof_observed_post_restart_boundary_closes_young_process() -> None:
    import time

    row = _row("git_integration_worker", _SHA_A, proof_class="process_live")
    settle_not_before = time.monotonic() - 30.0
    after = {"code_version": _SHA_A, "uptime_s": 2.0, "pid": 526100}
    assert proof_observed(
        row,
        after,
        settle_not_before_monotonic=settle_not_before,
    )


def test_ac_d3_drain_propagation_uptime_only_cannot_bind_process_identity() -> None:
    """Drain settle path: pre-fix liveness shape (no pid) fails strong identity."""
    import os
    import time
    from unittest.mock import patch

    from services.git_integration_worker.cursor_auto.liveness import AutoLivenessRegistry

    row = _row("git_integration_worker", _SHA_A, proof_class="process_live")
    settle_not_before = time.monotonic() - 30.0

    pre_fix_liveness = {"code_version": _SHA_A, "uptime_s": 2.0}
    assert pre_fix_liveness.get("pid") is None
    assert process_identity(pre_fix_liveness) == "uptime:2.000000"
    assert not strong_process_identity(pre_fix_liveness)
    assert not proof_observed(
        row,
        pre_fix_liveness,
        settle_not_before_monotonic=settle_not_before,
    )

    reg = AutoLivenessRegistry()
    reg.register("probe-handler")
    with patch(
        "services.git_integration_worker.cursor_auto.liveness.resolve_code_version",
        return_value=_SHA_A,
    ):
        post_fix = reg.snapshot()
    assert post_fix["pid"] == os.getpid()
    assert strong_process_identity(post_fix)
    assert process_identity(post_fix) == f"pid:{os.getpid()}"
    assert proof_observed(
        row,
        post_fix,
        settle_not_before_monotonic=settle_not_before,
    )


def test_proof_observed_post_restart_boundary_rejects_outgoing_generation() -> None:
    import time

    row = _row("git_integration_worker", _SHA_A, proof_class="process_live")
    settle_not_before = time.monotonic()
    after = {"code_version": _SHA_A, "uptime_s": 600.0}
    assert not proof_observed(
        row,
        after,
        settle_not_before_monotonic=settle_not_before,
    )


def test_client_visible_mcp_requires_both_surfaces() -> None:
    row = _row("mcp", _SHA_A, proof_class="client_visible")
    both_match = {
        "mcp_health": {"code_version": _SHA_A},
        "cortex_api": {"code_version": _SHA_A},
    }
    assert proof_observed(row, both_match) is True


def test_client_visible_mcp_instance2_replay_mcp_only_match() -> None:
    """agent-bus:6608 INSTANCE 2 — mcp health match alone must not close."""
    row = _row("mcp", _SHA_A, proof_class="client_visible")
    mcp_only = {
        "mcp_health": {"code_version": _SHA_A},
        "cortex_api": {"code_version": _SHA_STALE},
    }
    assert proof_observed(row, mcp_only) is False


def test_client_visible_mcp_missing_cortex_api() -> None:
    row = _row("mcp", _SHA_A, proof_class="client_visible")
    assert proof_observed(
        row,
        {"mcp_health": {"code_version": _SHA_A}, "cortex_api": None},
    ) is False


def test_client_visible_mcp_flat_payload_rejected() -> None:
    row = _row("mcp", _SHA_A, proof_class="client_visible")
    assert proof_observed(row, {"code_version": _SHA_A}) is False
