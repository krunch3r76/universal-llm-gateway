"""Caller-door prelude: skip wakes, restage CDP prompts, rewrite SDK packets."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from implement_admission.prompt_expand_admit import PIPELINE_ID

from systems.frontier_consult.cursor_sdk_prepared_handle import (
    PreparedCursorSdkHandle,
)
from systems.frontier_consult.prompt_expand_prelude import (
    ExpandRun,
    apply_expand_to_handle,
    maybe_expand_cdp_prompt,
    sdk_should_expand,
)


def _handle(**overrides: object) -> PreparedCursorSdkHandle:
    base = PreparedCursorSdkHandle(
        request_id="req",
        execution_id="exec-1",
        dispatch_id="disp-1",
        thread_id="11690",
        resolved_model="cursor/composer-2.5",
        role="cursor-sdk",
        family="cursor",
        platform="sdk",
        to_agent="cursor-sdk",
        handoff_contract="implement",
        packet_path=None,
        message="# Next-Seat Dispatch Packet\n\nFix the catalog skip.",
        caller_agent="web-anthropic",
        read_only=False,
        aligned_knobs=None,
        prompt_preamble=None,
        thread_subject="hop",
        pointer_body="",
        effective_bus_lifecycle="ephemeral",
        parent_dispatch_thread_id="10479",
        dispatch_thread_id="11690",
        density_triage=None,
        review_opt_out_reason_code=None,
        auto_review_child=False,
        auto_review_defaulted=False,
        claimed_via_atomic=False,
        admitted=True,
        alignment_warnings=(),
        knob_resolution=(),
    )
    return replace(base, **overrides) if overrides else base


@pytest.mark.offline
def test_sdk_should_expand_10479_implement() -> None:
    assert sdk_should_expand(_handle()) is True


@pytest.mark.offline
def test_sdk_should_expand_10479_sketch() -> None:
    assert sdk_should_expand(_handle(handoff_contract="sketch")) is True


@pytest.mark.offline
def test_sdk_should_not_expand_wake_message() -> None:
    handle = _handle(message="WAKE — liaison headless successor, house agent-bus:10479")
    assert sdk_should_expand(handle) is False


@pytest.mark.offline
def test_maybe_expand_cdp_restages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "in.md"
    src.write_text("TASK body for a 10479 consult", encoding="utf-8")
    staged_uri = "cortex://notes/system/ephemeral/out.md"

    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.read_prompt_uri",
        lambda uri: src.read_text(encoding="utf-8"),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt=f"---\npipeline: {PIPELINE_ID}\n---\n" + task,
            execution_id="exp-1",
        ),
    )

    class _Staged:
        prompt_uri = staged_uri

    monkeypatch.setattr(
        "claude_bundles.cdp_model_endpoint_staging.stage_prompt_uri",
        lambda **kwargs: _Staged(),
    )
    out = maybe_expand_cdp_prompt(
        prompt_uri="cortex://notes/system/ephemeral/in.md",
        execution_id="gen-1",
        thread_id="10479",
        parent_thread="10479",
        caller_agent="web-anthropic",
        contract="consult",
        model_id="cdp/opus-5",
    )
    assert out == staged_uri


@pytest.mark.offline
def test_apply_expand_rewrites_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(ok=True, prompt="TASK'", execution_id="exp-2"),
    )
    out = apply_expand_to_handle(_handle())
    assert out.message == "TASK'"
    assert out.execution_id == "exec-1"
