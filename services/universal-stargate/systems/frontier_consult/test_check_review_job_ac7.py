"""Acceptance item 7: check-review is job-keyed, not model-keyed.

Breaks when an omitted model is admitted, when grok-4.7 selects skeptic
delivery or skips FILE_EVIDENCE_PATHS, or when freeform takes that grammar.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.responses import Response

from implement_admission.check_review_substrate import CURSOR_CHECK_REVIEW_MODELS
from systems.frontier_consult._frontier_intake import reject_unsupported_packet_inputs
from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.cursor_sdk_role_delivery import closeout_for_job
from systems.frontier_consult.generate_wrap import dispatch_cursor_sdk_generate_route
from systems.frontier_consult.route import TeamDispatchGenerateBody

pytestmark = pytest.mark.offline


def test_symbols_absent() -> None:
    import implement_admission.check_review_substrate as substrate
    import systems.frontier_consult.cursor_sdk_role_delivery as delivery

    assert not hasattr(substrate, "cursor_delivery_from_role")
    assert not hasattr(delivery, "should_bridge_cursor_check_review")


def test_omitted_model_refused_at_stargate_intake() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        reject_unsupported_packet_inputs(
            request_id="r-ac7-omit",
            op="generate",
            contract="check-review",
            packet_path=None,
            source_ref=None,
            model=None,
        )
    assert exc.value.status_code == 422
    assert exc.value.details is not None
    assert exc.value.details["event"] == "dispatch.job.refused"
    assert exc.value.details["reason"] == "handle_forbidden"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", sorted(CURSOR_CHECK_REVIEW_MODELS))
async def test_allowlist_model_returns_reviewer_fields(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    async def _prompt(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(text="findings", prompt_turn_number=None, prompt_bind_mode=None)

    async def _threads(**kwargs: object) -> tuple[None, None, bool]:
        return None, None, False

    async def _generate(**kwargs: object) -> dict[str, str]:
        return {"execution_id": "exec-ac7", "status": "running"}

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_generate_prompt_resolution",
        _prompt,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_cursor_sdk_thread_targets",
        _threads,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        _generate,
    )
    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        job="check-review",
        model=model,
        lane="B",
        prompt="Review the diff.",
    )
    response = Response()
    result = await dispatch_cursor_sdk_generate_route(
        request_id="req-ac7",
        body=body,
        seat="cursor-sdk",
        response=response,
    )
    assert 200 <= response.status_code < 300
    assert result["execution_id"] == "exec-ac7"
    assert result["resolved_job"] == "check-review"
    assert result["delivery_role"] == "reviewer"
    assert result["registry_ref"] == "job_vocab:check-review"
    shaped = closeout_for_job("check-review", "Findings ok.")
    assert "FILE_EVIDENCE_PATHS:" in shaped


@pytest.mark.asyncio
async def test_freeform_same_model_skips_review_grammar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _prompt(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(text="findings", prompt_turn_number=None, prompt_bind_mode=None)

    async def _threads(**kwargs: object) -> tuple[None, None, bool]:
        return None, None, False

    async def _generate(**kwargs: object) -> dict[str, str]:
        return {"execution_id": "exec-free", "status": "running"}

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_generate_prompt_resolution",
        _prompt,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_cursor_sdk_thread_targets",
        _threads,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        _generate,
    )
    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        job="freeform",
        model="cursor/grok-4.7",
        lane="B",
        prompt="Do the work.",
    )
    response = Response()
    result = await dispatch_cursor_sdk_generate_route(
        request_id="req-ac7-free",
        body=body,
        seat="cursor-sdk",
        response=response,
    )
    assert result["resolved_job"] == "freeform"
    assert result["delivery_role"] == ""
    plain = closeout_for_job("freeform", "Findings ok.")
    assert "FILE_EVIDENCE_PATHS:" not in plain
