"""S2 tests — escalation wire + CDP commission (todo:cursor-auto-cdp-escalation-binding)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.cdp_escalation import (
    PROMPT_SOURCE_BODY,
    PROMPT_SOURCE_BRIEF,
    PROMPT_SOURCE_OVERRIDE,
    PROMPT_SOURCE_URI,
    commission_cdp_escalation,
    escalation_lane_refusal,
    resolve_cdp_escalation_prompt,
)
from services.git_integration_worker.cursor_auto.directive import body_escalation
from services.git_integration_worker.cursor_auto.job_ledger import AutoJobLedger
from services.git_integration_worker.cursor_auto.queue import AutoJob, AutoJobQueue
from services.git_integration_worker.cursor_auto.wire_map import (
    _MODEL_TABLE,
    BINDABLE_CDP_ESCALATIONS,
    assess_escalation_pin,
    resolve_escalation,
)


def test_model_table_has_zero_cdp_rows():
    """AC-S2-table: _MODEL_TABLE stays cursor-sdk-only."""
    assert not any(key.startswith("cdp/") or "fable" in key for key in _MODEL_TABLE)
    assert not any(val.startswith("cdp/") for val in _MODEL_TABLE.values())


def test_resolve_escalation_absent():
    out = resolve_escalation(None)
    assert out["resolved_escalation"] is None
    assert out["honored"] is False


def test_resolve_escalation_honors_bindable():
    out = resolve_escalation("cdp/fable")
    assert out["honored"] is True
    assert out["resolved_escalation"] == "cdp/fable"
    sonnet = resolve_escalation("cdp/sonnet")
    assert sonnet["honored"] is True
    assert sonnet["resolved_escalation"] == "cdp/sonnet-5"


def test_resolve_escalation_rejects_unknown():
    out = resolve_escalation("cdp/unknown")
    assert out.get("rejected") is True
    assert out["resolved_escalation"] is None


def test_assess_escalation_pin_blocks_body_mirror():
    """AC-S2-body-mirror: body escalation detected and refused."""
    esc, block = assess_escalation_pin(
        "cdp/fable",
        body="TYPE: DIRECTIVE\nescalation: cdp/opus-5\n",
    )
    assert block is not None
    assert "wire-only" in block
    assert body_escalation("TYPE: DIRECTIVE\nescalation: cdp/opus-5\n") == "cdp/opus-5"


def test_assess_escalation_pin_refuses_unknown_wire():
    esc, block = assess_escalation_pin("fable-5", body="")
    assert block is not None
    assert "unknown escalation" in block
    for value in BINDABLE_CDP_ESCALATIONS:
        assert value in block


def test_escalation_lane_refusal_hard_advisory():
    rows = [
        {"purpose": "operator-proxy", "status": "running"},
        {"purpose": "operator-proxy", "status": "running"},
        {"purpose": "ask", "status": "running"},
    ]
    refuse, lane = escalation_lane_refusal(
        {"rows": rows, "at_hard_limit": True, "at_soft_limit": True, "free_slots": 0},
        unattended=True,
    )
    assert refuse is False
    assert lane is None


def test_escalation_lane_refusal_soft_unattended_advisory():
    rows = [
        {"purpose": "operator-proxy", "status": "running"},
        {"purpose": "ask", "status": "running"},
    ]
    refuse, lane = escalation_lane_refusal(
        {"rows": rows, "at_hard_limit": False, "at_soft_limit": True, "free_slots": 1},
        unattended=True,
    )
    assert refuse is False
    assert lane is None


def test_escalation_lane_refusal_soft_attended_ok():
    refuse, lane = escalation_lane_refusal(
        {"at_hard_limit": False, "at_soft_limit": True, "free_slots": 1},
        unattended=False,
    )
    assert refuse is False
    assert lane is None


@pytest.mark.asyncio
async def test_commission_cdp_escalation_posts_team_dispatch():
    """AC-S2-wire: handler boundary posts Stargate team/dispatch once."""
    job = AutoJob(
        job_id="j-cdp",
        thread_id="6829",
        turn_number=1,
        subject="escalation test",
        body="TYPE: DIRECTIVE\n## Scope\nfoo\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="high",
        escalation="cdp/fable",
        contract="answer",
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"execution_id": "exec-cdp-1", "status": "started"}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "services.git_integration_worker.cursor_auto.cdp_escalation.make_async_client",
        return_value=mock_client,
    ):
        result = await commission_cdp_escalation(
            job,
            model="cdp/fable",
            reasoning_effort="high",
            stargate_url="http://stargate.test",
        )

    assert result["ok"] is True
    assert result["execution_id"] == "exec-cdp-1"
    mock_client.post.assert_awaited_once()
    call_args = mock_client.post.await_args
    assert call_args.args[0] == "/api/v1/team/dispatch"
    body = call_args.kwargs["json"]
    assert body["op"] == "generate"
    assert body["model"] == "cdp/fable"
    assert body["prompt"] == job.body
    assert body["reasoning_effort"] == "high"
    assert body["dispatch_thread_id"] == "6829"


def test_escalation_survives_ledger_roundtrip(tmp_path, monkeypatch):
    """AC-S2-wire: escalation persists through ledger restore."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    AutoJobLedger.reset_for_tests()
    queue = AutoJobQueue(durable=True)
    job = queue.enqueue(
        thread_id="6829",
        turn_number=1,
        subject="ledger",
        body="body",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        escalation="cdp/fable",
        contract="answer",
    )
    restored = AutoJobLedger.instance().list_open()[0]
    assert restored.escalation == "cdp/fable"
    assert restored.job_id == job.job_id


def test_process_job_commissions_cdp_on_answer(monkeypatch):
    """AC-S2-wire: answer contract + escalation commissions one CDP leg."""
    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.tests.commission_spy import commission_spy

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    commission = commission_spy(execution_id="exec-1")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.commission_cdp_escalation",
        commission,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.read_cdp_lane_snapshot",
        lambda **_: {"at_hard_limit": False, "at_soft_limit": False, "free_slots": 2},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.emit_cdp_effort_bind",
        lambda **_: None,
    )

    job = AutoJob(
        job_id="j-answer-cdp",
        thread_id="6829",
        turn_number=1,
        subject="cdp answer",
        body="Please summarize the escalation binding spec.",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        escalation="cdp/fable",
        contract="answer",
    )
    result = asyncio.run(process_job(job, bus=bus))
    assert result["terminal_status"] == "status:done"
    commission.assert_awaited_once()
    assert commission.await_args.kwargs["model"] == "cdp/fable"
    assert "reasoning_effort" in commission.await_args.kwargs


def test_process_job_cdp_escalation_proceeds_at_reported_hard_limit(monkeypatch):
    """Hard-limit telemetry no longer blocks unattended CDP escalation."""
    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.tests.commission_spy import commission_spy

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    commission = commission_spy(execution_id="exec-at-hard")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.commission_cdp_escalation",
        commission,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.read_cdp_lane_snapshot",
        lambda **_: {
            "rows": [
                {"purpose": "operator-proxy", "status": "running"},
                {"purpose": "operator-proxy", "status": "running"},
                {"purpose": "ask", "status": "running"},
            ],
            "at_hard_limit": True,
            "at_soft_limit": True,
            "free_slots": 0,
        },
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.emit_cdp_effort_bind",
        lambda **_: None,
    )

    job = AutoJob(
        job_id="j-lane-full",
        thread_id="6829",
        turn_number=1,
        subject="lane full",
        body="Please summarize the escalation binding spec.",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        escalation="cdp/fable",
        contract="answer",
    )
    result = asyncio.run(process_job(job, bus=bus))
    assert result["terminal_status"] == "status:done"
    commission.assert_awaited_once()


def test_process_job_desired_model_cdp_coalesces_to_escalation(monkeypatch):
    """desired_model=cdp/fable auto-moves onto escalation= so admits proceed."""
    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.tests.commission_spy import commission_spy

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    commission = commission_spy(execution_id="exec-coalesce")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.commission_cdp_escalation",
        commission,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.read_cdp_lane_snapshot",
        lambda **_: {"at_hard_limit": False, "at_soft_limit": False, "free_slots": 2},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.emit_cdp_effort_bind",
        lambda **_: None,
    )

    job = AutoJob(
        job_id="j-model-cdp-coalesce",
        thread_id="6829",
        turn_number=1,
        subject="coalesce fable pin",
        body="Please summarize the escalation binding spec.",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="cdp/fable",
        desired_effort="medium",
        contract="answer",
    )
    result = asyncio.run(process_job(job, bus=bus))
    assert result["terminal_status"] == "status:done"
    assert job.desired_model == "auto"
    assert job.escalation == "cdp/fable"
    commission.assert_awaited_once()
    assert commission.await_args.kwargs["model"] == "cdp/fable"
    assert "reasoning_effort" in commission.await_args.kwargs


def test_commission_spy_rejects_omitted_reasoning_effort():
    """Phase 6 acceptance: omitting reasoning_effort= fails loudly."""
    import asyncio

    from services.git_integration_worker.tests.commission_spy import commission_spy

    spy = commission_spy()
    job = AutoJob(
        job_id="j-omit",
        thread_id="1",
        turn_number=1,
        subject="omit",
        body="x",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="answer",
    )

    async def _call_without_effort() -> None:
        await spy(job, model="cdp/opus-5", purpose="operator-proxy")

    with pytest.raises(AssertionError, match="without reasoning_effort"):
        asyncio.run(_call_without_effort())


def test_process_job_cdp_effort_unclamped_when_sdk_model_non_roaming(monkeypatch):
    """CDP leg gets wire xhigh; sdk knobs follow the model card separately."""
    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.tests.commission_spy import commission_spy

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    commission = commission_spy(execution_id="exec-xhigh")
    binds: list[dict] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.commission_cdp_escalation",
        commission,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.read_cdp_lane_snapshot",
        lambda **_: {"at_hard_limit": False, "at_soft_limit": False, "free_slots": 2},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.emit_cdp_effort_bind",
        lambda **kwargs: binds.append(kwargs),
    )

    job = AutoJob(
        job_id="j-cdp-xhigh-unclamped",
        thread_id="6829",
        turn_number=1,
        subject="cdp xhigh",
        body="Please summarize the escalation binding spec.",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="cursor/claude-opus-5",
        desired_effort="xhigh",
        escalation="cdp/opus-5",
        contract="answer",
    )
    result = asyncio.run(process_job(job, bus=bus))
    assert result["terminal_status"] == "status:done"
    commission.assert_awaited_once()
    assert commission.await_args.kwargs["reasoning_effort"] == "xhigh"
    assert binds and binds[0]["resolved_effort"] == "xhigh"
    assert binds[0]["requested_effort"] == "xhigh"


def _directive_job(**overrides: object) -> AutoJob:
    payload: dict[str, object] = {
        "job_id": "j-brief",
        "thread_id": "9530",
        "turn_number": 1,
        "subject": "G1",
        "body": "TYPE: DIRECTIVE\n## Scope\nexecutor packet\n",
        "from_agent": "cursor-auto",
        "to_agent": "cursor",
        "desired_model": "auto",
        "desired_effort": "high",
        "escalation": "cdp/fable",
        "contract": "answer",
    }
    payload.update(overrides)
    return AutoJob(**payload)  # type: ignore[arg-type]


def test_resolve_falls_back_to_job_body_when_no_brief() -> None:
    job = _directive_job()
    prompt, source = resolve_cdp_escalation_prompt(job)
    assert source == PROMPT_SOURCE_BODY
    assert prompt == job.body


def test_resolve_uses_advisor_brief_not_job_body() -> None:
    sealed = "TYPE: CONSULT\nsealed advisor brief\n"
    job = _directive_job(advisor_brief=sealed)
    prompt, source = resolve_cdp_escalation_prompt(job)
    assert source == PROMPT_SOURCE_BRIEF
    assert prompt == sealed
    assert "TYPE: DIRECTIVE" not in prompt


def test_resolve_prompt_override_beats_brief() -> None:
    job = _directive_job(advisor_brief="sealed")
    prompt, source = resolve_cdp_escalation_prompt(
        job, prompt_override="hop successor body"
    )
    assert source == PROMPT_SOURCE_OVERRIDE
    assert prompt == "hop successor body"


def test_resolve_loads_prompt_uri(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    brief_path = tmp_path / "notes" / "system" / "threads" / "sealed.md"
    brief_path.parent.mkdir(parents=True)
    brief_path.write_text("TYPE: CONSULT\nuri-loaded brief\n", encoding="utf-8")
    from implement_admission import closeout_helpers

    monkeypatch.setattr(closeout_helpers, "cortex_files_root", lambda: tmp_path)
    uri = "cortex://notes/system/threads/sealed.md"
    job = _directive_job(prompt_uri=uri)
    prompt, source = resolve_cdp_escalation_prompt(job)
    assert source == PROMPT_SOURCE_URI
    assert prompt == "TYPE: CONSULT\nuri-loaded brief\n"
    assert job.body not in prompt


def test_resolve_missing_prompt_uri_fails_closed(tmp_path, monkeypatch) -> None:
    from implement_admission import closeout_helpers

    monkeypatch.setattr(closeout_helpers, "cortex_files_root", lambda: tmp_path)
    job = _directive_job(prompt_uri="cortex://notes/system/threads/missing.md")
    with pytest.raises(ValueError, match="prompt_uri not found"):
        resolve_cdp_escalation_prompt(job)


@pytest.mark.asyncio
async def test_commission_posts_advisor_brief_not_job_body() -> None:
    sealed = "TYPE: CONSULT\nsealed advisor brief\n"
    job = _directive_job(advisor_brief=sealed)
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"execution_id": "exec-brief", "status": "started"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "services.git_integration_worker.cursor_auto.cdp_escalation.make_async_client",
        return_value=mock_client,
    ):
        result = await commission_cdp_escalation(job, model="cdp/fable")
    assert result["ok"] is True
    body = mock_client.post.await_args.kwargs["json"]
    assert body["prompt"] == sealed
    assert body["prompt"] != job.body


@pytest.mark.asyncio
async def test_commission_unreadable_brief_does_not_send_job_body() -> None:
    job = _directive_job(prompt_uri="cortex://notes/system/threads/missing.md")
    mock_client = AsyncMock()
    mock_client.post = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "services.git_integration_worker.cursor_auto.cdp_escalation.make_async_client",
        return_value=mock_client,
    ):
        result = await commission_cdp_escalation(job, model="cdp/fable")
    assert result["ok"] is False
    assert result["reason"] == "advisor_brief_unreadable"
    mock_client.post.assert_not_awaited()
