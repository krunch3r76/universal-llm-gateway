"""Acceptance item 8: intake checks handles against the JobRecord set.

Breaks when job=mechanical admits source_ref, when job=implement admits an
inline prompt or omits source_ref, or when a legal mechanical prompt never
returns resolved_job.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from job_vocab.records import job_record
from starlette.responses import Response

from systems.frontier_consult._frontier_intake import reject_unsupported_packet_inputs
from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.generate_wrap import dispatch_cursor_sdk_generate_route
from systems.frontier_consult.route import TeamDispatchGenerateBody

pytestmark = pytest.mark.offline


def _refused(**kwargs: object) -> FrontierEndpointError:
    with pytest.raises(FrontierEndpointError) as exc:
        reject_unsupported_packet_inputs(
            request_id="r-ac8",
            op="generate",
            packet_path=None,
            sidecar_ref=None,
            **kwargs,  # type: ignore[arg-type]
        )
    return exc.value


def test_mechanical_handle_set_and_source_ref_refusal() -> None:
    record = job_record("mechanical")
    assert record is not None
    assert record.handle_set == frozenset({"prompt", "packet_path", "sidecar_ref"})
    reject_unsupported_packet_inputs(
        request_id="r-ac8-ok",
        op="generate",
        contract="mechanical",
        packet_path=None,
        source_ref=None,
        prompt="do the mechanical edit",
        sidecar_ref=None,
    )
    exc = _refused(contract="mechanical", source_ref="todo:x", prompt=None)
    assert exc.status_code == 422
    assert exc.code == "handle_forbidden"
    assert exc.field == "source_ref"
    assert exc.details is not None
    assert exc.details["event"] == "dispatch.job.refused"
    assert exc.details["reason"] == "handle_forbidden"


def test_implement_refuses_inline_prompt_and_requires_source_ref() -> None:
    record = job_record("implement")
    assert record is not None
    assert record.handle_set == frozenset({"source_ref"})
    prompt_exc = _refused(
        contract="implement",
        source_ref="todo:x",
        prompt="edit this inline",
    )
    assert prompt_exc.status_code == 422
    assert prompt_exc.code == "handle_forbidden"
    assert prompt_exc.field == "prompt"
    assert prompt_exc.details is not None
    assert prompt_exc.details["event"] == "dispatch.job.refused"
    assert prompt_exc.details["reason"] == "handle_forbidden"
    missing = _refused(contract="implement", source_ref=None, prompt=None)
    assert missing.status_code == 422
    assert missing.code == "handle_forbidden"
    assert missing.field == "source_ref"
    assert missing.details is not None
    assert missing.details["reason"] == "handle_forbidden"
    reject_unsupported_packet_inputs(
        request_id="r-ac8-impl",
        op="generate",
        contract="implement",
        packet_path=None,
        source_ref="todo:x",
        prompt=None,
        sidecar_ref=None,
    )


@pytest.mark.asyncio
async def test_mechanical_generate_returns_resolved_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _prompt(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            text="edit",
            prompt_turn_number=None,
            prompt_bind_mode=None,
        )

    async def _threads(**kwargs: object) -> tuple[None, None, bool]:
        return None, None, False

    async def _generate(**kwargs: object) -> dict[str, str]:
        return {"execution_id": "exec-ac8", "status": "running"}

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
        job="mechanical",
        lane="B",
        prompt="Apply the named edit.",
    )
    response = Response()
    result = await dispatch_cursor_sdk_generate_route(
        request_id="req-ac8",
        body=body,
        seat="cursor-sdk",
        response=response,
    )
    assert 200 <= response.status_code < 300
    assert result["execution_id"] == "exec-ac8"
    assert result["resolved_job"] == "mechanical"
