"""API-role generate review-target admit hook tests (hermetic)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Response

from .admission import FrontierEndpointError
from .dispatch_thread_context import GeneratePromptResolution
from .route import TeamDispatchGenerateBody


def _init_hub_with_lane(hub: Path, thread: str) -> None:
    subprocess.check_call(["git", "init", "-b", "master"], cwd=hub)
    subprocess.check_call(["git", "config", "user.email", "t@t.com"], cwd=hub)
    subprocess.check_call(["git", "config", "user.name", "t"], cwd=hub)
    (hub / "README.md").write_text("m\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "README.md"], cwd=hub)
    subprocess.check_call(["git", "commit", "-qm", "m"], cwd=hub)
    branch = f"cursor-sdk/lane-{thread}"
    subprocess.check_call(["git", "checkout", "-b", branch], cwd=hub)
    (hub / "lane.txt").write_text("lane\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "lane.txt"], cwd=hub)
    subprocess.check_call(["git", "commit", "-qm", "lane"], cwd=hub)
    subprocess.check_call(["git", "checkout", "master"], cwd=hub)


def _patches(prompt: str, *, hub: Path, wt_root: Path):
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
        patch(
            "systems.frontier_consult.api_role_generate.resolve_generate_thread_targets",
            new_callable=AsyncMock,
            return_value=(None, None, False, 0),
        ),
        patch.dict(
            "os.environ",
            {
                "GIT_INTEGRATION_SOURCE_REPO": str(hub),
                "GIT_INTEGRATION_WORKTREE_ROOT": str(wt_root),
            },
            clear=False,
        ),
    )


@pytest.mark.asyncio
async def test_lane_present_review_target_block(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    wt = tmp_path / "wt"
    hub.mkdir()
    wt.mkdir()
    _init_hub_with_lane(hub, "22520")
    (wt / "universal-llm-gateway" / "lane-22520").mkdir(parents=True)

    prompt = "review_thread: 22520\n"
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        job="code-review",
        model="anthropic/claude-sonnet-5-5",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    patches = _patches(prompt, hub=hub, wt_root=wt)
    with (
        patches[0],
        patches[1] as dispatch,
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
        patches[7],
    ):
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req-rt",
            body=body,
            response=response,
        )

    assert "review_target" in result
    assert result["review_target"]["thread"] == "22520"
    assert "## review_target" in dispatch.await_args.args[0].messages[0]["content"]
    assert 'thread="22520"' in dispatch.await_args.args[0].messages[0]["content"]


@pytest.mark.asyncio
async def test_merged_lane_no_op(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    wt = tmp_path / "wt"
    hub.mkdir()
    _init_hub_with_lane(hub, "1")
    subprocess.check_call(["git", "merge", "cursor-sdk/lane-1", "-m", "m"], cwd=hub)

    prompt = "review_thread: 1\n"
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        job="code-review",
        model="anthropic/claude-sonnet-5-5",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    patches = _patches(prompt, hub=hub, wt_root=wt)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req-merged",
            body=body,
            response=response,
        )

    assert "review_target" not in result


@pytest.mark.asyncio
async def test_worktree_missing_422(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    wt = tmp_path / "wt"
    hub.mkdir()
    wt.mkdir()
    _init_hub_with_lane(hub, "2")

    prompt = "review_thread: 2\n"
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        job="code-review",
        model="anthropic/claude-sonnet-5-5",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    patches = _patches(prompt, hub=hub, wt_root=wt)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        from .api_role_generate import dispatch_api_role_generate

        with pytest.raises(FrontierEndpointError) as exc_info:
            await dispatch_api_role_generate(
                request_id="req-miss",
                body=body,
                response=response,
            )
    assert exc_info.value.details["kind"] == "target"


@pytest.mark.asyncio
async def test_ambiguous_422(tmp_path: Path) -> None:
    prompt = "cursor-sdk/lane-1\n cursor-sdk/lane-2\n"
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        job="code-review",
        model="anthropic/claude-sonnet-5-5",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    hub = tmp_path / "hub"
    hub.mkdir()
    patches = _patches(prompt, hub=hub, wt_root=tmp_path / "wt")
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        from .api_role_generate import dispatch_api_role_generate

        with pytest.raises(FrontierEndpointError) as exc_info:
            await dispatch_api_role_generate(
                request_id="req-amb",
                body=body,
                response=response,
            )
    assert exc_info.value.details["kind"] == "ambiguous"


@pytest.mark.asyncio
async def test_freeform_untouched(tmp_path: Path) -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        job="freeform",
        prompt="review_thread: 1\n",
        caller_agent="cursor",
    )
    response = Response()
    patches = _patches("x", hub=tmp_path / "h", wt_root=tmp_path / "w")
    with patches[0], patches[1] as dispatch, patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req-ff",
            body=body,
            response=response,
        )
    assert "review_target" not in result
    assert "## review_target" not in dispatch.await_args.args[0].messages[0]["content"]


@pytest.mark.asyncio
async def test_bogus_explicit_head_422(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    wt = tmp_path / "wt"
    hub.mkdir()
    _init_hub_with_lane(hub, "3")
    (wt / "universal-llm-gateway" / "lane-3").mkdir(parents=True)

    prompt = "review_thread: 3\nreview_head: not-a-ref\n"
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        job="code-review",
        model="anthropic/claude-sonnet-5-5",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    patches = _patches(prompt, hub=hub, wt_root=wt)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        from .api_role_generate import dispatch_api_role_generate

        with pytest.raises(FrontierEndpointError) as exc_info:
            await dispatch_api_role_generate(
                request_id="req-bogus",
                body=body,
                response=response,
            )
    assert exc_info.value.details["kind"] == "target"


@pytest.mark.asyncio
async def test_git_infra_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    _init_hub_with_lane(hub, "4")
    import review_layer.hook as hook_mod

    def _boom(**_kwargs: object) -> None:
        from review_layer.git_io import ReviewLayerInfraError

        raise ReviewLayerInfraError("git down")

    monkeypatch.setattr(hook_mod, "resolve_review_target", _boom)
    prompt = "review_thread: 4\n"
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        job="code-review",
        model="anthropic/claude-sonnet-5-5",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    patches = _patches(prompt, hub=hub, wt_root=tmp_path / "wt")
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        from .api_role_generate import dispatch_api_role_generate

        with pytest.raises(FrontierEndpointError) as exc_info:
            await dispatch_api_role_generate(
                request_id="req-infra",
                body=body,
                response=response,
            )
    assert exc_info.value.details["kind"] == "infra"


@pytest.mark.asyncio
async def test_review_head_only_merged_no_op(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    _init_hub_with_lane(hub, "10")
    subprocess.check_call(["git", "merge", "cursor-sdk/lane-10", "-m", "m"], cwd=hub)
    head = subprocess.check_output(
        ["git", "rev-parse", "master"], cwd=hub, text=True
    ).strip()
    prompt = f"review_head: {head}\n"
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        job="code-review",
        model="anthropic/claude-sonnet-5-5",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    patches = _patches(prompt, hub=hub, wt_root=tmp_path / "wt")
    with patches[0], patches[1] as dispatch, patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req-head-ok",
            body=body,
            response=response,
        )
    assert "review_target" not in result
    assert "## review_target" not in dispatch.await_args.args[0].messages[0]["content"]


@pytest.mark.asyncio
async def test_review_head_only_unmerged_422(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    _init_hub_with_lane(hub, "11")
    head = subprocess.check_output(
        ["git", "rev-parse", "cursor-sdk/lane-11"], cwd=hub, text=True
    ).strip()
    prompt = f"review_head: {head}\n"
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        job="check-review",
        model="anthropic/claude-sonnet-5-5",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    patches = _patches(prompt, hub=hub, wt_root=tmp_path / "wt")
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        from .api_role_generate import dispatch_api_role_generate

        with pytest.raises(FrontierEndpointError) as exc_info:
            await dispatch_api_role_generate(
                request_id="req-head-bad",
                body=body,
                response=response,
            )
    assert exc_info.value.details["kind"] == "target"


@pytest.mark.asyncio
async def test_delivery_review_contract(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    wt = tmp_path / "wt"
    hub.mkdir()
    wt.mkdir()
    _init_hub_with_lane(hub, "6")
    (wt / "universal-llm-gateway" / "lane-6").mkdir(parents=True)
    prompt = "review_thread: 6\n"
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        job="delivery-review",
        model="anthropic/claude-sonnet-5-5",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    patches = _patches(prompt, hub=hub, wt_root=wt)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        from .api_role_generate import dispatch_api_role_generate

        result = await dispatch_api_role_generate(
            request_id="req-del",
            body=body,
            response=response,
        )
    assert "review_target" in result


@pytest.mark.asyncio
async def test_source_repo_non_git_infra(tmp_path: Path) -> None:
    not_git = tmp_path / "plain"
    not_git.mkdir()
    prompt = "review_head: deadbeef\n"
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        job="code-review",
        model="anthropic/claude-sonnet-5-5",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    patches = _patches(prompt, hub=not_git, wt_root=tmp_path / "wt")
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        from .api_role_generate import dispatch_api_role_generate

        with pytest.raises(FrontierEndpointError) as exc_info:
            await dispatch_api_role_generate(
                request_id="req-norepo",
                body=body,
                response=response,
            )
    assert exc_info.value.details["kind"] == "infra"


@pytest.mark.asyncio
async def test_hook_import_infra_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import sys

    real_import = builtins.__import__

    def broken_import(name: str, *args: object, **kwargs: object):
        if name == "review_layer.hook":
            raise ImportError("simulated hook import failure")
        return real_import(name, *args, **kwargs)

    sys.modules.pop("review_layer.hook", None)
    monkeypatch.setattr(builtins, "__import__", broken_import)
    hub = tmp_path / "hub"
    hub.mkdir()
    _init_hub_with_lane(hub, "20")
    prompt = "review_thread: 20\n"
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        job="code-review",
        model="anthropic/claude-sonnet-5-5",
        prompt=prompt,
        caller_agent="cursor",
    )
    response = Response()
    patches = _patches(prompt, hub=hub, wt_root=tmp_path / "wt")
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        from .api_role_generate import dispatch_api_role_generate

        with pytest.raises(FrontierEndpointError) as exc_info:
            await dispatch_api_role_generate(
                request_id="req-import",
                body=body,
                response=response,
            )
    assert exc_info.value.details["kind"] == "infra"


@pytest.mark.asyncio
async def test_implement_before_hook(tmp_path: Path) -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="thread:dispatch:test",
        job="implement",
        prompt="review_thread: 1\n",
        caller_agent="cursor",
    )
    response = Response()
    from .api_role_generate import dispatch_api_role_generate

    with pytest.raises(FrontierEndpointError) as exc_info:
        await dispatch_api_role_generate(
            request_id="req-imp",
            body=body,
            response=response,
        )
    assert exc_info.value.field == "contract"
