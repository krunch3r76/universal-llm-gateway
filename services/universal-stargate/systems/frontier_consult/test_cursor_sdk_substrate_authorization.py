"""Fork A falsifier: cursor model id cannot bypass SDK substrate authorization."""

from __future__ import annotations

import pytest
from fastapi import Response
from pydantic import ValidationError

from .admission import FrontierEndpointError, resolve_cursor_sdk_generate_target
from .conftest import dispatch_cursor_sdk_generate_mock
from .route import TeamDispatchGenerateBody, team_dispatch


def test_cloud_role_with_cursor_model_rejects_sdk_substrate_required() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        resolve_cursor_sdk_generate_target(
            "reviewer",
            model="cursor/claude-sonnet-5",
            request_id="req-fork-a",
        )
    err = exc_info.value
    assert err.code in {"sdk_substrate_required", "seat_unknown"}
    assert err.status_code == 422


@pytest.mark.asyncio
async def test_team_dispatch_cloud_role_cursor_model_rejects_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # role is a registry job alias; reviewer is not a job id.
    with pytest.raises(ValidationError) as excinfo:
        TeamDispatchGenerateBody(
            op="generate",
            role="reviewer",
            model="cursor/claude-sonnet-5",
            dispatch_thread_id="todo:arc",
            job="freeform",
        )
    assert "not a registry job" in str(excinfo.value)
    assert "reviewer" in str(excinfo.value)


@pytest.mark.asyncio
async def test_cursor_sdk_role_with_cursor_model_still_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_mock = dispatch_cursor_sdk_generate_mock(autospec=True,
        return_value={"execution_id": "exec-1", "thread_id": "t1"}
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        sdk_mock,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap._resolve_packet_file",
        lambda _root, _path: __import__("pathlib").Path("/tmp/packet.md"),
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        model="cursor/claude-sonnet-5",
        dispatch_thread_id="todo:arc",
        job="freeform",
        lane="A",
        packet_path="tmp/reviews/packet.md",
    )
    result = await team_dispatch(body, Response())

    # Spec fork 18: generate return carries resolved_job, delivery_role, registry_ref.
    assert result == {
        "execution_id": "exec-1",
        "thread_id": "t1",
        "resolved_job": "freeform",
        "delivery_role": "",
        "registry_ref": "job_vocab:freeform",
    }
    sdk_mock.assert_awaited_once()
