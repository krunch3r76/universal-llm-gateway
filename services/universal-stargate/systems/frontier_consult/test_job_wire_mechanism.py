"""Witnesses for the job-wire mechanism repair (G6 amend).

Each test locks one production behavior: resolved_contract reads ``job``,
freeform hypothesizes, CDP ignores a leftover ``contract`` attribute, and
omitted or unknown ``job`` is refused.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import Any

import pytest
from claude_bundles.cdp_model_endpoint import CdpGenerateResult
from fastapi import Response
from job_vocab import HYPOTHESIZE_ON_JOBS, job_record
from pydantic import ValidationError

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.cdp_generate_worker import run_cdp_worker
from systems.frontier_consult.route import (
    TeamDispatchGenerateBody,
    TeamDispatchToThreadBody,
    _normalize_op_body,
    team_dispatch,
)

pytestmark = pytest.mark.offline


def test_generate_body_job_sets_resolved_contract_without_contract_attr() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="gatherer",
        dispatch_thread_id="dispatch-thread-1",
        prompt="hello",
        job="confer",
    )
    assert "contract" not in type(body).model_fields
    assert not hasattr(body, "contract")
    kwargs = _normalize_op_body(body)
    assert kwargs["resolved_contract"] == body.job == "confer"


def test_freeform_record_hypothesize_on() -> None:
    record = job_record("freeform")
    assert record is not None
    assert record.hypothesize_on is True
    assert "freeform" in HYPOTHESIZE_ON_JOBS


def _consult_attribute_body() -> SimpleNamespace:
    """Job absent. A leftover ``contract`` attribute must not be read."""
    return SimpleNamespace(
        op="generate",
        model="cdp/opus-5",
        prompt="hop with job omitted",
        mission_kind="hop",
        contract="consult",
        job=None,
        seat=None,
        role=None,
        dispatch_lane=None,
        dispatch_thread_id="11165",
        sidecar_ref=None,
        packet_path=None,
        skills=None,
        purpose=None,
        parent_thread=None,
        caller_agent="cursor",
        bus_lifecycle=None,
        timeout_seconds=None,
        generation_options=None,
        predecessor_registration_id=None,
        reasoning_effort=None,
    )


@pytest.mark.asyncio
async def test_cdp_ignores_contract_consult_when_job_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult import cdp_generate_worker as worker_mod
    from systems.frontier_consult import prompt_expand_prelude as prelude

    monkeypatch.setattr(
        mod,
        "_stage_inputs",
        lambda **kw: MagicMock(
            prompt_uri="cortex://notes/system/ephemeral/prompt.md", staged=True
        ),
    )
    monkeypatch.setattr(mod, "post_pointer_turn", AsyncMock(return_value=2))
    monkeypatch.setattr(
        mod,
        "admit_handoff_dispatch",
        AsyncMock(return_value=MagicMock(reason="ok")),
    )
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    monkeypatch.setattr(
        mod,
        "build_handoff_result",
        lambda **kw: {
            "handoff_status": "ok",
            "poll_hint": {"thread_id": "1", "from_agent": "web-anthropic"},
        },
    )
    monkeypatch.setattr(mod, "resolve_poll_wait_seconds", lambda **kw: 5)
    monkeypatch.setattr(worker_mod, "publish_cdp_kwargs", lambda *a, **k: None)

    expanded: dict[str, Any] = {}

    def _record_expand(**kwargs: Any) -> str:
        expanded["contract"] = kwargs["contract"]
        return str(kwargs["prompt_uri"])

    def _fake_generate(**kwargs: Any) -> CdpGenerateResult:
        return CdpGenerateResult(
            ok=True,
            body="ok",
            execution_id=str(kwargs["execution_id"]),
            satellite_execution_id=None,
            prompt_uri=str(kwargs["prompt_uri"]),
            picker_model="opus-5",
        )

    monkeypatch.setattr(prelude, "maybe_expand_cdp_prompt", _record_expand)
    monkeypatch.setattr(worker_mod, "run_cdp_generate", _fake_generate)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.finalize_cdp_generate",
        AsyncMock(return_value=None),
    )

    observed: dict[str, Any] = {}
    pending: list[Any] = []
    real_sig = inspect.signature(run_cdp_worker)

    async def _spy(**kwargs: Any) -> None:
        bound = real_sig.bind(**kwargs)
        bound.apply_defaults()
        observed["bound_contract"] = bound.arguments["contract"]
        observed["call_had_contract_kwarg"] = "contract" in kwargs
        await run_cdp_worker(**kwargs)

    class _FakeTask:
        def add_done_callback(self, _cb: object) -> None:
            return None

    def _capture_task(coro: object, **kwargs: object) -> _FakeTask:
        pending.append(coro)
        return _FakeTask()

    monkeypatch.setattr(mod, "run_cdp_worker", _spy)
    monkeypatch.setattr(mod.asyncio, "create_task", _capture_task)

    response = Response()
    result = await mod.dispatch_cdp_generate(
        request_id="req-consult-attr",
        body=_consult_attribute_body(),  # type: ignore[arg-type]
        response=response,
    )
    assert pending, "dispatch did not schedule run_cdp_worker"
    await pending[0]

    assert observed["bound_contract"] is None
    assert observed["call_had_contract_kwarg"] is False
    assert expanded.get("contract") is None
    assert result["resolved_job"] is None


@pytest.mark.asyncio
async def test_cdp_refuses_implement_on_job_not_contract_attr() -> None:
    from systems.frontier_consult.cdp_generate import dispatch_cdp_generate

    body = SimpleNamespace(
        model="cdp/opus-5",
        job="implement",
        contract="consult",
        seat=None,
        role=None,
        dispatch_lane=None,
        prompt="x",
        reasoning_effort=None,
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await dispatch_cdp_generate(
            request_id="req-impl-job",
            body=body,  # type: ignore[arg-type]
            response=Response(),
        )
    assert exc.value.code == "cdp_contract_unsupported"


def test_stargate_intake_refuses_omitted_job_and_consult() -> None:
    from systems.frontier_consult._frontier_intake import (
        reject_unsupported_packet_inputs,
    )

    with pytest.raises(FrontierEndpointError) as omitted:
        reject_unsupported_packet_inputs(
            request_id="r-omit",
            op="generate",
            contract=None,
            packet_path=None,
            source_ref=None,
        )
    assert omitted.value.field == "job"
    assert omitted.value.code == "job_missing"
    assert omitted.value.status_code == 422
    assert omitted.value.details is not None
    assert omitted.value.details["event"] == "dispatch.job.refused"
    assert omitted.value.details["reason"] == "job_missing"
    assert omitted.value.details["registry_ref"] == "job_vocab:unresolved"
    assert "(omitted)" in omitted.value.reason

    with pytest.raises(FrontierEndpointError) as consult:
        reject_unsupported_packet_inputs(
            request_id="r-consult",
            op="generate",
            contract="consult",
            packet_path=None,
            source_ref=None,
        )
    assert consult.value.field == "job"
    assert consult.value.status_code == 422
    assert consult.value.code == "job_unknown"
    assert consult.value.details is not None
    assert consult.value.details["event"] == "dispatch.job.refused"
    assert consult.value.details["reason"] == "job_unknown"
    assert consult.value.details["registry_ref"] == "job_vocab:unresolved"
    assert "job_retired" not in consult.value.reason

    for retired in ("none", "pure-mechanical"):
        with pytest.raises(FrontierEndpointError) as unknown:
            reject_unsupported_packet_inputs(
                request_id=f"r-{retired}",
                op="generate",
                contract=retired,
                packet_path=None,
                source_ref=None,
            )
        assert unknown.value.status_code == 422
        assert unknown.value.code == "job_unknown"
        assert unknown.value.details is not None
        assert unknown.value.details["event"] == "dispatch.job.refused"
        assert unknown.value.details["reason"] == "job_unknown"
        assert unknown.value.details["registry_ref"] == "job_vocab:unresolved"
        assert unknown.value.details["reason"] != "job_retired"

    with pytest.raises(FrontierEndpointError) as to_thread:
        reject_unsupported_packet_inputs(
            request_id="r-thread",
            op="to_thread",
            contract=None,
            packet_path=None,
            source_ref=None,
        )
    assert to_thread.value.code == "job_missing"
    assert to_thread.value.details is not None
    assert to_thread.value.details["reason"] == "job_missing"


def test_validating_constructor_rejects_omitted_and_consult() -> None:
    with pytest.raises(ValidationError) as omitted:
        TeamDispatchGenerateBody(
            op="generate",
            role="gatherer",
            dispatch_thread_id="dt-1",
            prompt="hello",
        )
    assert any(err["loc"] == ("job",) for err in omitted.value.errors())

    with pytest.raises(ValidationError) as consult:
        TeamDispatchGenerateBody(
            op="generate",
            role="gatherer",
            dispatch_thread_id="dt-1",
            prompt="hello",
            job="consult",  # type: ignore[arg-type]
        )
    assert any(err["loc"] == ("job",) for err in consult.value.errors())

    with pytest.raises(ValidationError) as to_thread:
        TeamDispatchToThreadBody(
            op="to_thread",
            role="gatherer",
            dispatch_thread_id="dt-1",
            thread="867",
            prompt="hello",
            job="consult",  # type: ignore[arg-type]
        )
    assert any(err["loc"] == ("job",) for err in to_thread.value.errors())


@pytest.mark.asyncio
async def test_route_passes_job_and_refuses_omitted_and_consult(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import systems.frontier_consult._frontier_intake as intake

    seen: list[str | None] = []
    real = intake.reject_unsupported_packet_inputs

    def _wrap(**kwargs: Any) -> None:
        seen.append(kwargs["contract"])
        return real(**kwargs)

    monkeypatch.setattr(intake, "reject_unsupported_packet_inputs", _wrap)

    consult = await team_dispatch(
        TeamDispatchGenerateBody.model_construct(
            op="generate",
            role="gatherer",
            dispatch_thread_id="dt-1",
            prompt="hello",
            job="consult",
        ),
        Response(),
    )
    consult_body = json.loads(consult.body)
    assert consult.status_code == 422
    assert consult_body["field"] == "job"
    assert consult_body["error"]["code"] == "job_unknown"
    assert consult_body["details"]["event"] == "dispatch.job.refused"
    assert consult_body["details"]["reason"] == "job_unknown"
    assert consult_body["details"]["registry_ref"] == "job_vocab:unresolved"
    assert seen == ["consult"]

    omitted = await team_dispatch(
        TeamDispatchGenerateBody.model_construct(
            op="generate",
            role="gatherer",
            dispatch_thread_id="dt-1",
            prompt="hello",
        ),
        Response(),
    )
    omitted_body = json.loads(omitted.body)
    assert omitted.status_code == 422
    assert omitted_body["error"]["code"] == "job_missing"
    assert omitted_body["details"]["reason"] == "job_missing"
    assert omitted_body["details"]["registry_ref"] == "job_vocab:unresolved"
    assert seen == ["consult", None]

    to_thread = await team_dispatch(
        TeamDispatchToThreadBody.model_construct(
            op="to_thread",
            role="gatherer",
            dispatch_thread_id="dt-1",
            thread="867",
            prompt="hello",
            job="consult",
        ),
        Response(),
    )
    to_thread_body = json.loads(to_thread.body)
    assert to_thread.status_code == 422
    assert to_thread_body["error"]["code"] == "job_unknown"
    assert to_thread_body["details"]["reason"] == "job_unknown"
    assert to_thread_body["details"]["registry_ref"] == "job_vocab:unresolved"
    assert seen == ["consult", None, "consult"]


@pytest.mark.asyncio
async def test_freeform_omitted_session_admits_without_quoted_purpose_briefing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """job=freeform, no session: 2xx, execution_id, registry_ref.

    A prompt line ``purpose=operator-proxy`` does not open the hop briefing
    or the cdp-operator-proxy chip. Breaks when staging still scans the prompt
    or when omitted purpose is inferred as ask.
    """
    from unittest.mock import AsyncMock, MagicMock

    from claude_bundles import cdp_model_endpoint_staging as staging

    from systems.frontier_consult import cdp_generate as mod

    monkeypatch.setattr(staging, "ephemeral_dir", lambda _eid: tmp_path)
    monkeypatch.setattr(staging, "cortex_files_root", lambda: tmp_path)
    monkeypatch.setattr(mod, "post_pointer_turn", AsyncMock(return_value=2))
    monkeypatch.setattr(
        mod,
        "admit_handoff_dispatch",
        AsyncMock(return_value=MagicMock(reason="ok")),
    )
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    monkeypatch.setattr(
        mod,
        "build_handoff_result",
        lambda **kw: {
            "handoff_status": "ok",
            "poll_hint": {"thread_id": "1", "from_agent": "web-anthropic"},
        },
    )
    monkeypatch.setattr(mod, "resolve_poll_wait_seconds", lambda **kw: 5)
    monkeypatch.setattr(mod, "record_cdp_admit", lambda **kw: None)

    seen: dict[str, Any] = {}

    async def _worker(**kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(mod, "run_cdp_worker", _worker)

    prompt = "purpose=operator-proxy\nDo the work.\n"
    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/opus-5.5",
        dispatch_thread_id="14394",
        prompt=prompt,
    )
    response = Response()
    result = await mod.dispatch_cdp_generate(
        request_id="req-ac3",
        body=body,
        response=response,
    )
    assert 200 <= response.status_code < 300
    assert result["execution_id"]
    assert result["resolved_job"] == "freeform"
    assert result["registry_ref"] == "job_vocab:freeform"
    assert seen.get("purpose") is None
    staged = (tmp_path / "prompt.md").read_text(encoding="utf-8")
    assert "Mission seat map" not in staged
    assert "cdp-operator-proxy" not in staged
    assert prompt.splitlines()[0] in staged
    assert "reasoning-posture" in staged


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session",
    ["operator-proxy", "mission"],
)
async def test_session_operator_proxy_or_mission_admits_briefing_and_chip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    session: str,
) -> None:
    """session=operator-proxy|mission: briefing and cdp-operator-proxy chip.

    Breaks when the session field is dropped before staging, or when the
    floor comes from a prompt line instead of cdp_skill_profiles.
    """
    result, staged = await _admit_cdp_session(
        monkeypatch, tmp_path, session=session, request_id=f"req-{session}"
    )
    assert result["resolved_job"] == "freeform"
    assert "Mission seat map" in staged
    assert "cdp-operator-proxy" in staged


@pytest.mark.asyncio
async def test_session_ask_admits_architecture_floor_without_chip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """session=ask: architecture floor, no operator-proxy briefing or chip.

    Breaks when ask is treated as a mission purpose.
    """
    _result, staged = await _admit_cdp_session(
        monkeypatch, tmp_path, session="ask", request_id="req-ask"
    )
    assert "architecture-invariants" in staged
    assert "ulg-architecture" in staged
    assert "Mission seat map" not in staged
    assert "cdp-operator-proxy" not in staged


@pytest.mark.asyncio
async def test_session_operator_proxy_underscore_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """session=operator_proxy: intake dispatch.job.refused session_unknown.

    Breaks when the underscore spelling is folded into operator-proxy and
    staging injects the briefing. Service-down is out of this path: the
    refuse is raised before CDP admit.
    """
    import systems.frontier_consult._frontier_intake as intake

    seen: list[str | None] = []
    real = intake.reject_unsupported_packet_inputs

    def _wrap(**kwargs: Any) -> None:
        seen.append(kwargs.get("session"))
        return real(**kwargs)

    monkeypatch.setattr(intake, "reject_unsupported_packet_inputs", _wrap)

    refused = await team_dispatch(
        TeamDispatchGenerateBody(
            op="generate",
            job="freeform",
            model="cdp/opus-5.5",
            dispatch_thread_id="14394",
            prompt="Do the work.\n",
            session="operator_proxy",
        ),
        Response(),
    )
    body = json.loads(refused.body)
    assert refused.status_code == 422
    assert body["field"] == "session"
    assert body["error"]["code"] == "session_unknown"
    assert body["details"]["event"] == "dispatch.job.refused"
    assert body["details"]["reason"] == "session_unknown"
    assert seen == ["operator_proxy"]


async def _admit_cdp_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    *,
    session: str,
    request_id: str,
) -> tuple[dict[str, Any], str]:
    from unittest.mock import AsyncMock, MagicMock

    from claude_bundles import cdp_model_endpoint_staging as staging

    from systems.frontier_consult import cdp_generate as mod

    monkeypatch.setattr(staging, "ephemeral_dir", lambda _eid: tmp_path)
    monkeypatch.setattr(staging, "cortex_files_root", lambda: tmp_path)
    monkeypatch.setattr(mod, "post_pointer_turn", AsyncMock(return_value=2))
    monkeypatch.setattr(
        mod,
        "admit_handoff_dispatch",
        AsyncMock(return_value=MagicMock(reason="ok")),
    )
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    monkeypatch.setattr(
        mod,
        "build_handoff_result",
        lambda **kw: {
            "handoff_status": "ok",
            "poll_hint": {"thread_id": "1", "from_agent": "web-anthropic"},
        },
    )
    monkeypatch.setattr(mod, "resolve_poll_wait_seconds", lambda **kw: 5)
    monkeypatch.setattr(mod, "record_cdp_admit", lambda **kw: None)

    async def _worker(**kwargs: Any) -> None:
        del kwargs

    monkeypatch.setattr(mod, "run_cdp_worker", _worker)

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/opus-5.5",
        dispatch_thread_id="14394",
        prompt="Do the work.\n",
        session=session,
    )
    response = Response()
    result = await mod.dispatch_cdp_generate(
        request_id=request_id,
        body=body,
        response=response,
    )
    assert 200 <= response.status_code < 300
    assert result["execution_id"]
    staged = (tmp_path / "prompt.md").read_text(encoding="utf-8")
    return result, staged
