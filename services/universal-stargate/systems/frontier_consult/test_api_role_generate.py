"""Unit tests for API-role generate default bus delivery."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse

from .admission import FrontierEndpointError
from .route import TeamDispatchGenerateBody


@pytest.mark.asyncio
async def test_dispatch_api_role_generate_provisions_thread_and_to_thread() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        contract="light-bounded",
        model="anthropic/claude-sonnet-4-6",
        caller_agent="cursor",
    )
    response = Response()
    dispatch_payload = {
        "execution_id": "exec-1",
        "status": "running",
        "knob_resolution": {"resolved_model": "anthropic/claude-sonnet-4-6"},
        "capabilities": {"mcp_connector_active": True},
    }
    mock_profile = type("Profile", (), {"tool_surface": "mcp"})()

    with (
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
            return_value="1602",
        ) as create_thread,
        patch(
            "systems.frontier_consult.route._dispatch",
            new_callable=AsyncMock,
            return_value=dispatch_payload,
        ) as dispatch,
        patch(
            "systems.frontier_consult.api_role_generate._resolve_role_profile",
            return_value=("synthesizer", "anthropic", "api", mock_profile),
        ),
        patch(
            "systems.frontier_consult.api_role_generate.read_latest_dispatch_thread_body",
            new_callable=AsyncMock,
            return_value="ping",
        ),
    ):
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req123",
            body=body,
            response=response,
        )

    create_thread.assert_awaited_once()
    dispatch.assert_awaited_once()
    req = dispatch.await_args.args[0]
    assert req.output_contract == "thread"
    assert req.target_thread == "1602"
    assert req.op == "to_thread"

    assert result["thread_id"] == "1602"
    assert result["output_contract"] == "thread"
    assert result["op"] == "generate"
    assert result["poll_hint"]["tool"] == "wait"
    assert result["poll_hint"]["arguments"]["thread"] == "1602"
    assert result["poll_hint"]["arguments"]["from_agent"] == "synthesizer"
    assert result["result_handle"]["kind"] == "dual"
    assert result["result_handle"]["thread_id"] == "1602"
    assert result["result_handle"]["execution_id"] == "exec-1"


@pytest.mark.asyncio
async def test_dispatch_api_role_generate_empty_thread_raises_422() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        contract="light-bounded",
        caller_agent="cursor",
    )
    response = Response()

    from .api_role_generate import dispatch_api_role_generate

    with (
        patch(
            "systems.frontier_consult.api_role_generate.read_latest_dispatch_thread_body",
            new_callable=AsyncMock,
            side_effect=FrontierEndpointError(
                request_id="req-empty",
                field="dispatch_thread_id",
                reason="latest turn on dispatch thread is empty",
                status_code=422,
            ),
        ),
        pytest.raises(FrontierEndpointError) as exc_info,
    ):
        await dispatch_api_role_generate(
            request_id="req-empty",
            body=body,
            response=response,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.field == "dispatch_thread_id"


@pytest.mark.asyncio
async def test_dispatch_api_role_generate_implement_contract_raises_422() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        contract="implement",
        caller_agent="cursor",
    )
    response = Response()

    from .api_role_generate import dispatch_api_role_generate

    with pytest.raises(FrontierEndpointError) as exc_info:
        await dispatch_api_role_generate(
            request_id="req-impl",
            body=body,
            response=response,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.field == "contract"


@pytest.mark.asyncio
async def test_dispatch_api_role_generate_json_response_posts_failure_turn() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        contract="light-bounded",
        caller_agent="cursor",
    )
    response = Response()
    error_response = JSONResponse(
        status_code=422,
        content={"error": {"code": "persona_violation", "message": "rejected"}},
    )

    with (
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
            return_value="1605",
        ),
        patch(
            "systems.frontier_consult.route._dispatch",
            new_callable=AsyncMock,
            return_value=error_response,
        ),
        patch(
            "systems.frontier_consult.api_role_generate._post_api_role_dispatch_failure_turn",
            new_callable=AsyncMock,
        ) as failure_turn,
        patch(
            "systems.frontier_consult.api_role_generate.read_latest_dispatch_thread_body",
            new_callable=AsyncMock,
            return_value="ping",
        ),
    ):
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req-err",
            body=body,
            response=response,
        )

    failure_turn.assert_awaited_once_with(
        thread_id="1605",
        role="synthesizer",
        request_id="req-err",
        caller_agent="cursor",
    )
    assert result is error_response


@pytest.mark.asyncio
async def test_dispatch_api_role_generate_dict_error_posts_failure_turn() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        contract="light-bounded",
        caller_agent="cursor",
    )
    response = Response()
    error_payload = {"error": {"code": "dispatch_invalid_response", "message": "bad"}}

    with (
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
            return_value="1605",
        ),
        patch(
            "systems.frontier_consult.route._dispatch",
            new_callable=AsyncMock,
            return_value=error_payload,
        ),
        patch(
            "systems.frontier_consult.api_role_generate._post_api_role_dispatch_failure_turn",
            new_callable=AsyncMock,
        ) as failure_turn,
        patch(
            "systems.frontier_consult.api_role_generate.read_latest_dispatch_thread_body",
            new_callable=AsyncMock,
            return_value="ping",
        ),
    ):
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req-dict-err",
            body=body,
            response=response,
        )

    failure_turn.assert_awaited_once()
    assert result is error_payload
    assert result["error"]["code"] == "dispatch_invalid_response"


@pytest.mark.asyncio
async def test_dispatch_api_role_generate_capabilities_model_fallback() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        contract="light-bounded",
        caller_agent="cursor",
    )
    response = Response()
    dispatch_payload = {
        "execution_id": "exec-2",
        "status": "running",
        "capabilities": {"resolved_model": "anthropic/claude-opus-4-6"},
    }
    mock_profile = type("Profile", (), {"tool_surface": "mcp"})()

    with (
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
            return_value="1603",
        ),
        patch(
            "systems.frontier_consult.route._dispatch",
            new_callable=AsyncMock,
            return_value=dispatch_payload,
        ),
        patch(
            "systems.frontier_consult.api_role_generate._resolve_role_profile",
            return_value=("synthesizer", "anthropic", "api", mock_profile),
        ),
        patch(
            "systems.frontier_consult.api_role_generate.read_latest_dispatch_thread_body",
            new_callable=AsyncMock,
            return_value="ping",
        ),
    ):
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req-caps",
            body=body,
            response=response,
        )

    assert result["resolved_model"] == "anthropic/claude-opus-4-6"
