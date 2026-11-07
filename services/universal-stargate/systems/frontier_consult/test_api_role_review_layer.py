"""API-role generate review-layer admit hook tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Response

from .admission import FrontierEndpointError
from .dispatch_thread_context import GeneratePromptResolution
from .route import TeamDispatchGenerateBody


def _init_hub_with_lane_branch(hub: Path, lane_branch: str) -> None:
    subprocess.check_call(["git", "init", "-b", "master"], cwd=hub)
    subprocess.check_call(
        ["git", "config", "user.email", "test@example.com"], cwd=hub
    )
    subprocess.check_call(["git", "config", "user.name", "test"], cwd=hub)
    (hub / "README.md").write_text("master\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "README.md"], cwd=hub)
    subprocess.check_call(["git", "commit", "-m", "master"], cwd=hub)
    subprocess.check_call(["git", "checkout", "-b", lane_branch], cwd=hub)
    (hub / "lane.txt").write_text("lane\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "lane.txt"], cwd=hub)
    subprocess.check_call(["git", "commit", "-m", "lane"], cwd=hub)
    subprocess.check_call(["git", "checkout", "master"], cwd=hub)


def _patch_review_generate(prompt: str):
    mock_profile = type("Profile", (), {"tool_surface": "mcp"})()
    dispatch_payload = {
        "execution_id": "exec-review",
        "status": "running",
        "knob_resolution": {"resolved_model": "anthropic/claude-sonnet-5-5"},
    }
    return (
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
            return_value="9001",
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
            "systems.frontier_consult.api_role_generate.resolve_generate_prompt_resolution",
            new_callable=AsyncMock,
            return_value=GeneratePromptResolution(
                text=prompt, prompt_bind_mode="explicit_external"
            ),
        ),
        patch(
            "systems.frontier_consult.work_registry_admit_relay.relay_registry_record",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "systems.frontier_consult.api_role_generate.emit_poll_hint_from_handoff",
        ),
    )


@pytest.mark.asyncio
async def test_code_review_unmerged_lane_branch_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    cortex = tmp_path / "cortex"
    cortex.mkdir()
    lane = "cursor-sdk/lane-22520"
    _init_hub_with_lane_branch(hub, lane)
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(hub))
    monkeypatch.setattr(
        "review_layer.materialize.cortex_files_root", lambda: cortex
    )

    prompt = f"Review branch {lane} before land."
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="22520",
        job="code-review",
        model="anthropic/claude-sonnet-5-5",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    patches = _patch_review_generate(prompt)
    with (
        patches[0],
        patches[1] as dispatch,
        patches[2],
        patches[3],
        patches[4],
        patches[5],
    ):
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req-layer",
            body=body,
            response=response,
        )

    assert "review_layer" in result
    assert result["review_layer"]["uri"] == "cortex://tmp/review-layers/req-layer/"
    req = dispatch.await_args.args[0]
    user_text = req.messages[0]["content"]
    assert "## review_layer" in user_text
    assert (cortex / "tmp/review-layers/req-layer/manifest.json").is_file()


@pytest.mark.asyncio
async def test_code_review_bogus_lane_branch_422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    _init_hub_with_lane_branch(hub, "cursor-sdk/lane-1")
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(hub))

    prompt = "Review cursor-sdk/lane-99999."
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="1",
        job="code-review",
        model="anthropic/claude-sonnet-5-5",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    patches = _patch_review_generate(prompt)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        from .api_role_generate import dispatch_api_role_generate

        with pytest.raises(FrontierEndpointError) as exc_info:
            await dispatch_api_role_generate(
                request_id="req-bogus",
                body=body,
                response=response,
            )
    err = exc_info.value
    assert err.code == "REVIEW_TARGET_UNREACHABLE"
    assert err.status_code == 422
    assert err.details is not None
    assert "fix_hint" in err.details


@pytest.mark.asyncio
async def test_freeform_contract_skips_review_layer(tmp_path: Path) -> None:
    prompt = "cursor-sdk/lane-22520"
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="22520",
        job="freeform",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    patches = _patch_review_generate(prompt)
    with (
        patches[0],
        patches[1] as dispatch,
        patches[2],
        patches[3],
        patches[4],
        patches[5],
    ):
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req-freeform",
            body=body,
            response=response,
        )

    assert "review_layer" not in result
    req = dispatch.await_args.args[0]
    assert "## review_layer" not in req.messages[0]["content"]


@pytest.mark.asyncio
async def test_implement_contract_raises_before_review_layer(tmp_path: Path) -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="1",
        job="implement",
        prompt="cursor-sdk/lane-1",
        caller_agent="cursor",
    )
    response = Response()
    from .api_role_generate import dispatch_api_role_generate

    with pytest.raises(FrontierEndpointError) as exc_info:
        await dispatch_api_role_generate(
            request_id="req-implement",
            body=body,
            response=response,
        )
    assert exc_info.value.field == "contract"
