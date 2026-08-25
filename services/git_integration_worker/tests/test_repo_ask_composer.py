"""Repo-ask Composer PoC — ask contract, Auto defaults, read_only nest, fences."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from contract_vocab import nested_scope_contracts, vision_required_contracts
from reasoning_posture_contracts import REASONING_POSTURE_SKIP_CONTRACTS
from services.git_integration_worker.cursor_auto.admit_gates import blocking_admit_gate
from services.git_integration_worker.cursor_auto.handler import (
    _NESTED_CONTRACTS,
    process_job,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.static_pin_refusal import (
    assess_static_pin_refusal,
)
from services.git_integration_worker.cursor_auto.wire_map import (
    coalesce_cdp_desired_model_into_escalation,
    resolve_desired_effort,
    resolve_desired_model,
    resolve_handoff_contract,
)
from services.git_integration_worker.cursor_sdk_packet import resolve_prompt_preamble


def _ask_job(**kwargs: object) -> AutoJob:
    fields: dict[str, object] = dict(
        job_id="j-ask",
        thread_id="9594",
        turn_number=1,
        subject="ask: ULG — how-question",
        body="Kaywan wants ULG to be able to map satellite workspaces. Where?",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="ask",
    )
    fields.update(kwargs)
    return AutoJob(**fields)  # type: ignore[arg-type]


def _recon_directive(*, vision: bool) -> str:
    body = (
        "TYPE: DIRECTIVE\n"
        "density: dense\n"
        "## Scope\n"
        "libs/foo\n"
    )
    if vision:
        body += "vision: inventory the files that own this surface\n"
    return body


def test_ask_is_confer_pattern_not_nested_scope() -> None:
    assert "ask" in _NESTED_CONTRACTS
    assert "ask" not in nested_scope_contracts()
    assert "ask" not in vision_required_contracts()
    assert "ask" in REASONING_POSTURE_SKIP_CONTRACTS


def test_auto_ask_and_recon_choose_composer_medium() -> None:
    for contract in ("ask", "recon"):
        model = resolve_desired_model("auto", contract=contract)
        assert model["resolved_model_id"] == "cursor/composer-2.5"
        effort = resolve_desired_effort(None, contract=contract)
        assert effort["resolved_effort"] == "medium"
        assert effort["clamped"] is False


def test_ask_handoff_is_ask_not_answer() -> None:
    assert resolve_handoff_contract("ask") == "ask"
    assert resolve_handoff_contract("ask") != "answer"
    text = resolve_prompt_preamble(
        handoff_contract="ask",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "reasoning-posture" not in text


def test_ask_escalation_and_coalesced_cdp_refused() -> None:
    direct = assess_static_pin_refusal(
        desired_model="auto",
        desired_effort="medium",
        escalation="cdp/opus-5",
        contract="ask",
        body="plain how-question, no scope",
    )
    assert direct is not None
    assert direct.reason == "ask_escalation_unsupported"

    desired_model, escalation, meta = coalesce_cdp_desired_model_into_escalation(
        "cdp/opus-5",
        None,
    )
    assert meta.get("coalesced") is True
    coalesced = assess_static_pin_refusal(
        desired_model=desired_model,
        desired_effort="medium",
        escalation=escalation,
        contract="ask",
        body="plain how-question",
    )
    assert coalesced is not None
    assert coalesced.reason == "ask_escalation_unsupported"


def test_ask_without_escalation_is_not_statically_refused() -> None:
    refusal = assess_static_pin_refusal(
        desired_model="auto",
        desired_effort="medium",
        escalation=None,
        contract="ask",
        body="plain how-question, no scope, no vision",
    )
    assert refusal is None


def _bus_client() -> AsyncMock:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body="{}"))
    return client


@pytest.mark.asyncio
async def test_recon_still_blocks_missing_vision() -> None:
    job = AutoJob(
        job_id="j-recon-vision",
        thread_id="9594",
        turn_number=1,
        subject="recon inventory",
        body=_recon_directive(vision=False),
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="recon",
    )
    gate_out = await blocking_admit_gate(
        job,
        client=_bus_client(),
        queue=MagicMock(),
    )
    assert gate_out.blocked is not None
    assert "vision_field_missing" in str(gate_out.blocked.get("summary", ""))


def test_process_job_ask_nests_read_only_without_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    submit = AsyncMock(return_value={"ok": False, "error": "stop"})
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda **_: {"active": 0, "queued": 0, "limit": 1},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
        AsyncMock(return_value="active"),
    )

    asyncio.run(process_job(_ask_job(), bus=bus))
    submit.assert_awaited_once()
    assert submit.await_args.kwargs["read_only"] is True
    assert submit.await_args.kwargs["handoff_contract"] == "ask"


def test_process_job_answer_still_declines_in_seat(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    submit = AsyncMock()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda **_: {"active": 0, "queued": 0, "limit": 1},
    )
    job = _ask_job(
        job_id="j-answer",
        contract="answer",
        body="What is the handler status?",
        subject="status check",
    )
    result = asyncio.run(process_job(job, bus=bus))
    submit.assert_not_awaited()
    assert result["disposition"] == "declined"


def test_nested_post_read_only_and_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeCM:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> object:
            client = AsyncMock()

            async def _post(url: str, json: dict[str, object]) -> MagicMock:
                captured.update(json)
                resp = MagicMock()
                resp.status_code = 200
                resp.content = b'{"admitted": true}'
                resp.json.return_value = {"admitted": True}
                return resp

            client.post = _post
            return client

        async def __aexit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_sdk.httpx.AsyncClient",
        _FakeCM,
    )
    from services.git_integration_worker.cursor_auto.nested_sdk import (
        submit_nested_dispatch,
    )

    asyncio.run(
        submit_nested_dispatch(
            _ask_job(workspace="claudeburst"),
            model_id="cursor/composer-2.5",
            handoff_contract="ask",
            message="how does this live",
            read_only=True,
            bind_job=False,
        )
    )
    assert captured["read_only"] is True
    assert captured["workspace"] == "claudeburst"
    assert "lane" not in captured
