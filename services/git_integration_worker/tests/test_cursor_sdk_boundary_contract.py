"""Item 17 — boundary contract mechanism; unwrap boundary (attempt-12 falsifier)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_boundary_contract import (
    BoundaryContractError,
    BoundaryShapeViolation,
)
from services.git_integration_worker.cursor_sdk_boundary_unwrap import (
    UNWRAP_BOUNDARY,
    classify_tool_result_shape,
    emit_unwrap_boundary,
)
from services.git_integration_worker.cursor_sdk_tool_result import (
    assertion_id_from_payload,
    unwrap_tool_result,
)

pytestmark = pytest.mark.offline

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "item18_attempt9_live_obs_result.json"
_LIVE_ASSERTION_ID = 27489


def _load_mcp_fixture() -> dict[str, object]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _sdk_wrapper_from_mcp(mcp_body: dict[str, object]) -> dict[str, object]:
    """Live production SDK-wrapper shape from attempt-12 evidence."""
    inner_text = mcp_body["content"][0]["text"]  # type: ignore[index]
    return {
        "status": "success",
        "value": {
            "content": [
                {
                    "text": {
                        "text": inner_text,
                    }
                }
            ]
        },
    }


def _legacy_unwrap_without_sdk_value_content(result: object) -> object | None:
    """Pre-item-17 unwrap: short-circuits on ``value`` without nested content parse."""
    if not isinstance(result, dict):
        return unwrap_tool_result(result)
    if result.get("status") == "error":
        return None
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    value = result.get("value")
    if value is not None:
        if isinstance(value, str):
            return json.loads(value) if value.strip().startswith("{") else value
        return value
    content = result.get("content")
    if isinstance(content, list) and content:
        block = content[0]
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                return json.loads(text)
    return result


def test_ac17a_mcp_shape_validated_at_emit() -> None:
    mcp = _load_mcp_fixture()
    result = emit_unwrap_boundary(mcp)
    assert result.conforming
    assert result.shape_label == "mcp_content_text"
    assert assertion_id_from_payload(unwrap_tool_result(mcp)) == _LIVE_ASSERTION_ID


def test_ac17a_sdk_shape_validated_at_emit_after_fix() -> None:
    sdk = _sdk_wrapper_from_mcp(_load_mcp_fixture())
    result = emit_unwrap_boundary(sdk)
    assert result.conforming
    assert result.shape_label == "sdk_value_content_text_text"
    assert assertion_id_from_payload(unwrap_tool_result(sdk)) == _LIVE_ASSERTION_ID


def test_ac17b_violation_message_names_boundary_expected_arrived() -> None:
    sdk = _sdk_wrapper_from_mcp(_load_mcp_fixture())
    legacy_payload = _legacy_unwrap_without_sdk_value_content(sdk)
    assert legacy_payload is not None
    assert assertion_id_from_payload(legacy_payload) is None

    violation = BoundaryShapeViolation(
        boundary=UNWRAP_BOUNDARY,
        expected="harvestable payload with item.id",
        arrived="shape=sdk_value_content_text_text",
        detail="unwrap returned None for SDK-wrapper shape",
    )
    msg = violation.legible_message()
    assert "boundary=unwrap" in msg
    assert "expected=" in msg
    assert "arrived=" in msg


def test_ac17c_attempt12_defect_reproduced_and_caught_by_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falsifier: MCP fixture harvests; legacy unwrap on SDK wrapper fails contract."""
    mcp = _load_mcp_fixture()
    sdk = _sdk_wrapper_from_mcp(mcp)

    assert classify_tool_result_shape(mcp) == "mcp_content_text"
    assert classify_tool_result_shape(sdk) == "sdk_value_content_text_text"
    assert assertion_id_from_payload(unwrap_tool_result(mcp)) == _LIVE_ASSERTION_ID

    assert assertion_id_from_payload(_legacy_unwrap_without_sdk_value_content(sdk)) is None

    import services.git_integration_worker.cursor_sdk_boundary_unwrap as unwrap_mod

    monkeypatch.setattr(unwrap_mod, "unwrap_tool_result", _legacy_unwrap_without_sdk_value_content)

    with pytest.raises(BoundaryContractError) as exc_info:
        emit_unwrap_boundary(sdk, strict=True)

    msg = exc_info.value.violation.legible_message()
    assert "boundary=unwrap" in msg
    assert "sdk_value_content_text_text" in msg
    assert (
        "attempt-12" in msg
        or "unwrap returned None" in msg
        or "lacks harvestable assertion id" in msg
    )


def test_ac17c_fixed_unwrap_passes_contract_for_both_shapes() -> None:
    mcp = _load_mcp_fixture()
    sdk = _sdk_wrapper_from_mcp(mcp)
    for body in (mcp, sdk):
        result = emit_unwrap_boundary(body, strict=True)
        assert result.conforming
        assert assertion_id_from_payload(unwrap_tool_result(body)) == _LIVE_ASSERTION_ID


def test_unwrap_sdk_value_content_text_text_unit() -> None:
    inner = {"item": {"id": 42}, "status": "success"}
    sdk = {
        "status": "success",
        "value": {"content": [{"text": {"text": json.dumps(inner)}}]},
    }
    assert unwrap_tool_result(sdk) == inner
    assert assertion_id_from_payload(unwrap_tool_result(sdk)) == 42
