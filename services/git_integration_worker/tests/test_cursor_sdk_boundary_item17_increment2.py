"""Item 17 increment 2 — name_gate, join_key, retention, deployment_identity boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_boundary_contract import (
    BoundaryContractError,
    BoundaryShapeViolation,
)
from services.git_integration_worker.cursor_sdk_boundary_deployment_identity import (
    DEPLOYMENT_IDENTITY_BOUNDARY,
    DeploymentIdentityEmit,
    emit_deployment_identity_boundary,
)
from services.git_integration_worker.cursor_sdk_boundary_join_key import (
    JOIN_KEY_BOUNDARY,
    emit_join_key_boundary,
)
from services.git_integration_worker.cursor_sdk_boundary_name_gate import (
    NAME_GATE_BOUNDARY,
    emit_name_gate_boundary,
)
from services.git_integration_worker.cursor_sdk_boundary_retention import (
    RETENTION_BOUNDARY,
    emit_retention_boundary,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
    resolve_stream_tool_name,
)
from services.git_integration_worker.cursor_sdk_toolcall_retention import (
    RESULT_BODY_PRESENT,
    ToolcallResultRetention,
    prepare_toolcall_result_retention,
)

pytestmark = pytest.mark.offline

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "item18_attempt9_live_obs_result.json"
_LIVE_ENTITY = "todo:ac9g-live-falsifier"
_ASSERTION_ID = 27486


def _production_mcp_args() -> dict[str, object]:
    return {
        "providerIdentifier": "user-vortex",
        "toolName": "cortex",
        "args": {
            "tool": "assert",
            "arguments": json.dumps({"entity_id": _LIVE_ENTITY}),
        },
    }


def _production_obs(*, call_id: str = "tool_a57a9066-82f0-43d1-b626-bdc3452edc6") -> ToolCallObservation:
    args = _production_mcp_args()
    return ToolCallObservation(
        call_id=call_id,
        tool_name=resolve_stream_tool_name("mcp", args),
        status="completed",
        arg_bytes=500,
        result_bytes=3208,
        truncated_fields=(),
        args=args,
        result={"status": "success", "value": {"item": {"id": _ASSERTION_ID}}},
    )


def test_ac17g_name_gate_violation_legible() -> None:
    violation = BoundaryShapeViolation(
        boundary=NAME_GATE_BOUNDARY,
        expected="resolved logical name from args.toolName (not 'mcp')",
        arrived="resolved='mcp' wire='mcp'",
        detail="resolve_stream_tool_name returned wire name",
    )
    msg = violation.legible_message()
    assert "boundary=name_gate" in msg
    assert "expected=" in msg
    assert "arrived=" in msg
    assert "detail=" in msg


def test_ac17h_name_gate_catches_mcp_cortex_mismatch() -> None:
    """Falsifier: pre-fix stream left tool_name=mcp — reconcile phantom divergences."""
    args = _production_mcp_args()
    with pytest.raises(BoundaryContractError) as exc_info:
        emit_name_gate_boundary("mcp", args, resolved_name="mcp", strict=True)
    msg = exc_info.value.violation.legible_message()
    assert "boundary=name_gate" in msg
    assert "mcp" in msg


def test_ac17h_name_gate_passes_after_resolve() -> None:
    args = _production_mcp_args()
    resolved = resolve_stream_tool_name("mcp", args)
    assert resolved == "cortex"
    result = emit_name_gate_boundary("mcp", args, resolved_name=resolved, strict=True)
    assert result.conforming


def test_ac17h_join_key_catches_entity_only_index() -> None:
    """Falsifier: attempt-7 index keyed entity slug but omitted call_id."""
    obs = _production_obs()
    incomplete = {_LIVE_ENTITY: str(_ASSERTION_ID)}
    with pytest.raises(BoundaryContractError) as exc_info:
        emit_join_key_boundary(incomplete, source_observations=(obs,), strict=True)
    msg = exc_info.value.violation.legible_message()
    assert "boundary=join_key" in msg
    assert "call_id" in msg


def test_ac17h_join_key_passes_dual_axis_index() -> None:
    obs = _production_obs()
    index = {_LIVE_ENTITY: str(_ASSERTION_ID), obs.call_id: str(_ASSERTION_ID)}
    result = emit_join_key_boundary(index, source_observations=(obs,), strict=True)
    assert result.conforming
    assert result.shape_label == "dual_axis"


def test_ac17h_retention_catches_metadata_only_legacy() -> None:
    """Falsifier: result_bytes without result_body_status (item-22 pre-fix class)."""
    legacy = ToolcallResultRetention(
        result_body=None,
        result_body_status="absent_legacy_metadata_only",
        result_retention_window_s=604800,
        result_retention_expires_at_unix_ms=0,
    )
    with pytest.raises(BoundaryContractError) as exc_info:
        emit_retention_boundary(legacy, result_bytes=3208, status="completed", strict=True)
    msg = exc_info.value.violation.legible_message()
    assert "boundary=retention" in msg


def test_ac17h_retention_passes_present_body() -> None:
    live_body = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    retention = prepare_toolcall_result_retention(
        live_body,
        truncated_fields=(),
        result_bytes=len(json.dumps(live_body)),
        status="completed",
    )
    result = emit_retention_boundary(
        retention,
        result_bytes=len(json.dumps(live_body)),
        status="completed",
        strict=True,
    )
    assert result.conforming
    assert retention.result_body_status == RESULT_BODY_PRESENT


def test_ac17i_deployment_identity_reattach_falsifier() -> None:
    """Green MCP relay probe while cdp-ask executor runs eight-hour-old code."""
    mcp_relay_health = {
        "code_version": "dbe810c6ac12d1ef9872a5a6a358aebe72053b6e",
        "pid": 999001,
        "status": "ok",
    }
    stale_executor = {
        "code_version": "a50a554c7b315633642ccadbc7366db74d026506",
        "pid": 2421162,
    }
    with pytest.raises(BoundaryContractError) as exc_info:
        emit_deployment_identity_boundary(
            DeploymentIdentityEmit(
                expected_executor="cdp_ask_satellite",
                probed_surface="mcp_relay",
                payload=mcp_relay_health,
                code_ref="dbe810c6ac12d1ef9872a5a6a358aebe72053b6e",
                before_payload=stale_executor,
            ),
            strict=True,
        )
    msg = exc_info.value.violation.legible_message()
    assert "boundary=deployment_identity" in msg
    assert "mcp_relay" in msg or "cdp_ask_satellite" in msg


def test_ac17i_deployment_identity_stale_pid_without_restart() -> None:
    """code_version match but same pid — would have passed relay-only verification."""
    sha = "dbe810c6ac12d1ef9872a5a6a358aebe72053b6e"
    before = {"code_version": "old_sha_other", "pid": 2421162}
    after = {"code_version": sha, "pid": 2421162}
    with pytest.raises(BoundaryContractError) as exc_info:
        emit_deployment_identity_boundary(
            DeploymentIdentityEmit(
                expected_executor="cdp_ask_satellite",
                probed_surface="cdp_ask_satellite",
                payload=after,
                code_ref=sha,
                before_payload=before,
            ),
            strict=True,
        )
    msg = exc_info.value.violation.legible_message()
    assert "boundary=deployment_identity" in msg
    assert "2421162" in msg or "pid" in msg


def test_ac17i_deployment_identity_passes_after_restart() -> None:
    sha = "dbe810c6ac12d1ef9872a5a6a358aebe72053b6e"
    before = {"code_version": "old_sha_other", "pid": 2421162}
    after = {"code_version": sha, "pid": 526100}
    result = emit_deployment_identity_boundary(
        DeploymentIdentityEmit(
            expected_executor="git_integration_worker",
            probed_surface="git_integration_worker",
            payload=after,
            code_ref=sha,
            before_payload=before,
        ),
        strict=True,
    )
    assert result.conforming
