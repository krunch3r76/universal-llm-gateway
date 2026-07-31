"""Unit tests for API-role generate default bus delivery."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse

from .admission import FrontierEndpointError
from .dispatch_thread_context import as_user_message
from .route import TeamDispatchGenerateBody

_PACKET_BODY = """<scope>
Review the adapter.
</scope>
<task_guidance>
Check imports.
</task_guidance>
"""


@pytest.mark.asyncio
async def test_dispatch_api_role_generate_packet_path_admits_with_packet_body(
    tmp_path,
) -> None:
    packet_file = tmp_path / "tmp" / "packet.md"
    packet_file.parent.mkdir(parents=True)
    packet_file.write_text(_PACKET_BODY, encoding="utf-8")

    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        contract="light-bounded",
        packet_path="tmp/packet.md",
        caller_agent="cursor",
    )
    response = Response()
    dispatch_payload = {
        "execution_id": "exec-packet",
        "status": "running",
        "knob_resolution": {"resolved_model": "anthropic/claude-sonnet-4-6"},
    }
    mock_profile = type("Profile", (), {"tool_surface": "mcp"})()

    with (
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
            return_value="1610",
        ),
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
            "systems.frontier_consult.handoff._workspaces_root",
            return_value=tmp_path,
        ),
        patch(
            "systems.frontier_consult.api_role_generate.read_latest_dispatch_thread_body",
            new_callable=AsyncMock,
        ) as read_thread,
    ):
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req-packet",
            body=body,
            response=response,
        )

    read_thread.assert_not_awaited()
    req = dispatch.await_args.args[0]
    assert req.messages == as_user_message(_PACKET_BODY)
    assert result["thread_id"] == "1610"


@pytest.mark.asyncio
async def test_dispatch_api_role_generate_packet_path_messages_equal_packet_body(
    tmp_path,
) -> None:
    packet_file = tmp_path / "tmp" / "packet.md"
    packet_file.parent.mkdir(parents=True)
    packet_file.write_text(_PACKET_BODY, encoding="utf-8")

    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        contract="light-bounded",
        packet_path="tmp/packet.md",
        model="anthropic/claude-sonnet-4-6",
        caller_agent="cursor",
    )
    response = Response()
    dispatch_payload = {
        "execution_id": "exec-packet",
        "status": "running",
        "knob_resolution": {"resolved_model": "anthropic/claude-sonnet-4-6"},
    }
    mock_profile = type("Profile", (), {"tool_surface": "mcp"})()

    with (
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
            return_value="1611",
        ),
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
            "systems.frontier_consult.handoff._workspaces_root",
            return_value=tmp_path,
        ),
        patch(
            "systems.frontier_consult.api_role_generate.read_latest_dispatch_thread_body",
            new_callable=AsyncMock,
        ) as read_thread,
    ):
        from .api_role_generate import dispatch_api_role_generate

        await dispatch_api_role_generate(
            request_id="req-packet-msg",
            body=body,
            response=response,
        )

    read_thread.assert_not_awaited()
    req = dispatch.await_args.args[0]
    assert req.messages == as_user_message(_PACKET_BODY)


@pytest.mark.asyncio
async def test_dispatch_api_role_generate_missing_packet_path_raises_422(
    tmp_path,
) -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        contract="light-bounded",
        packet_path="tmp/missing-packet.md",
        caller_agent="cursor",
    )
    response = Response()

    from .api_role_generate import dispatch_api_role_generate

    with (
        patch(
            "systems.frontier_consult.handoff._workspaces_root",
            return_value=tmp_path,
        ),
        pytest.raises(FrontierEndpointError) as exc_info,
    ):
        await dispatch_api_role_generate(
            request_id="req-missing-packet",
            body=body,
            response=response,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.field == "packet_path"
    assert exc_info.value.code == "packet_path_unreadable"


@pytest.mark.asyncio
async def test_dispatch_api_role_generate_source_ref_still_raises_422() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        contract="light-bounded",
        source_ref="todo:sample",
        caller_agent="cursor",
    )
    response = Response()

    from .api_role_generate import dispatch_api_role_generate

    with pytest.raises(FrontierEndpointError) as exc_info:
        await dispatch_api_role_generate(
            request_id="req-source-ref",
            body=body,
            response=response,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.field == "source_ref"
    assert exc_info.value.code == "packet_not_supported_for_api_role"


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
    assert result["result_handle"]["durable"] is False


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


@pytest.mark.asyncio
async def test_api_generate_default_on_recommended_review() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        contract="light-bounded",
        density_triage="judgment_required",
        caller_agent="cursor",
    )
    response = Response()
    dispatch_payload = {"execution_id": "exec-3", "status": "running", "capabilities": {"resolved_model": "anthropic/claude-sonnet-4-6"}}
    mock_profile = type("Profile", (), {"tool_surface": "mcp"})()

    with (
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
            return_value="1604",
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
            request_id="req-density",
            body=body,
            response=response,
        )

    assert "recommended_review" in result
    assert result["recommended_review"] == "cross-family-reconcile:default-on"


@pytest.mark.asyncio
async def test_api_generate_trivial_present_null_review() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        contract="light-bounded",
        density_triage="trivial",
        caller_agent="cursor",
    )
    response = Response()
    dispatch_payload = {"execution_id": "exec-4", "status": "running", "capabilities": {"resolved_model": "anthropic/claude-sonnet-4-6"}}
    mock_profile = type("Profile", (), {"tool_surface": "mcp"})()

    with (
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
            return_value="1604",
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
            request_id="req-trivial",
            body=body,
            response=response,
        )

    assert "recommended_review" in result
    assert result["recommended_review"] is None


@pytest.mark.asyncio
async def test_api_role_generate_reuses_dispatch_prompt_thread() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="2683",
        contract="light-bounded",
        model="anthropic/claude-sonnet-4-6",
        caller_agent="cursor",
    )
    response = Response()
    dispatch_payload = {
        "execution_id": "exec-reuse",
        "status": "running",
        "knob_resolution": {"resolved_model": "anthropic/claude-sonnet-4-6"},
    }
    mock_profile = type("Profile", (), {"tool_surface": "mcp"})()

    with (
        patch(
            "systems.frontier_consult.api_role_generate.resolve_generate_thread_targets",
            new_callable=AsyncMock,
            return_value=("2683", None, True, 1),
        ),
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
        ) as create_thread,
        patch(
            "systems.frontier_consult.api_role_generate.post_pointer_turn",
            new_callable=AsyncMock,
            return_value=2,
        ) as post_pointer,
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
        patch(
            "systems.frontier_consult.api_role_generate._emit_dispatch_thread_event",
        ),
    ):
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req-reuse",
            body=body,
            response=response,
        )

    create_thread.assert_not_awaited()
    post_pointer.assert_awaited_once()
    assert post_pointer.await_args.kwargs["thread_id"] == "2683"
    assert result["thread_id"] == "2683"
    assert result["poll_hint"]["arguments"]["after_turn"] == 2
    req = dispatch.await_args.args[0]
    assert req.target_thread == "2683"
    assert req.bus_lifecycle == "persistent"


@pytest.mark.asyncio
async def test_api_role_generate_split_thread_mints_despite_reusable_arc() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="2683",
        contract="light-bounded",
        split_thread=True,
        caller_agent="cursor",
    )
    response = Response()
    dispatch_payload = {"execution_id": "exec-split", "status": "running", "capabilities": {"resolved_model": "anthropic/claude-sonnet-4-6"}}
    mock_profile = type("Profile", (), {"tool_surface": "mcp"})()

    with (
        patch(
            "systems.frontier_consult.api_role_generate.resolve_generate_thread_targets",
            new_callable=AsyncMock,
            return_value=(None, "2683", False, 0),
        ),
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
            return_value="1609",
        ) as create_thread,
        patch(
            "systems.frontier_consult.api_role_generate.post_pointer_turn",
            new_callable=AsyncMock,
        ) as post_pointer,
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
        patch(
            "systems.frontier_consult.api_role_generate._emit_dispatch_thread_event",
        ),
    ):
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req-split",
            body=body,
            response=response,
        )

    create_thread.assert_awaited_once()
    post_pointer.assert_not_awaited()
    assert result["thread_id"] == "1609"
    assert "warnings" not in result or not any(
        "split_thread" in w for w in (result.get("warnings") or [])
    )
