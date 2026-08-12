"""Tier-M scope grammar, blocked fix-hints, and answer no-op fix (Fable Option B)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto.admit_gates import blocking_admit_gate
from services.git_integration_worker.cursor_auto.directive import (
    empty_directive_missed_tokens,
    has_actionable_scope,
)
from claim_register import CLAIM_REGISTER_UNKNOWN

from services.git_integration_worker.cursor_auto.fix_hints import (
    EMPTY_SCOPE_FIX_HINT,
    VISION_MISSING_FIX_HINT,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    ANSWER_DECLINED_REASON,
    terminal_in_seat,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob

_TIER_M_DIRECTIVE = (
    "TYPE: DIRECTIVE\n"
    "contract: implement\n"
    "intent: fire dispatch(tool=\"email\", op=pull, folder=INBOX, limit=3)\n"
    "tool_op: email.pull\n"
    "effects_expected: raw pull JSON relayed inline in the closeout\n"
    "density: sparse\n"
    "vision: mechanical — tier-M surface asymmetry relay\n"
)
_BARE_DIRECTIVE = (
    "TYPE: DIRECTIVE\ndensity: sparse\nintent: do the email thing\n"
)


def _job(body: str, *, contract: str = "implement", job_id: str = "j-tier-m") -> AutoJob:
    return AutoJob(
        job_id=job_id,
        thread_id="6325",
        turn_number=1,
        subject="tier-M tool op",
        body=body,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="low",
        contract=contract,
    )


def test_tool_op_and_effects_expected_are_actionable_scope() -> None:
    assert has_actionable_scope(_TIER_M_DIRECTIVE)
    assert has_actionable_scope("tool_op: observability.query\n")
    assert has_actionable_scope("effects_expected: BEFORE/AFTER counts relayed\n")
    assert not has_actionable_scope(_BARE_DIRECTIVE)


def test_missed_tokens_enumerate_tier_m_tokens() -> None:
    missed_bare = empty_directive_missed_tokens(_BARE_DIRECTIVE)
    assert "tool_op" in missed_bare
    assert "effects_expected" in missed_bare
    missed_tier_m = empty_directive_missed_tokens(_TIER_M_DIRECTIVE)
    assert "tool_op" not in missed_tier_m
    assert "effects_expected" not in missed_tier_m


def _reply_payload(client: AsyncMock) -> dict:
    return json.loads(client.reply.await_args.kwargs["body"])


def test_tier_m_directive_passes_scope_and_vision_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
        AsyncMock(return_value="active"),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        AsyncMock(return_value=[]),
    )
    result = asyncio.run(
        blocking_admit_gate(
            _job(_TIER_M_DIRECTIVE),
            client=AsyncMock(),
            queue=MagicMock(),
        )
    )
    assert result.blocked is None


def test_empty_scope_blocked_payload_carries_fix_hint() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    result = asyncio.run(
        blocking_admit_gate(
            _job(_BARE_DIRECTIVE, job_id="j-no-scope"),
            client=client,
            queue=MagicMock(),
        )
    )
    assert result.blocked is not None
    assert result.blocked["terminal_status"] == "status:blocked"
    payload = _reply_payload(client)
    assert payload["reason"] == "empty_directive_scope"
    # Still bare at emit; post_terminal_status stamps unknown (Packet A
    # post-time degrade — does not retrofit every fix_hint site in S2).
    assert payload["fix_hint"]["register"] == CLAIM_REGISTER_UNKNOWN
    assert payload["fix_hint"]["value"] == EMPTY_SCOPE_FIX_HINT
    assert "tool_op" in payload["fix_hint"]["value"]


def test_vision_missing_blocked_payload_carries_fix_hint() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    body = "TYPE: DIRECTIVE\ndensity: sparse\ntool_op: email.pull\n"
    result = asyncio.run(
        blocking_admit_gate(
            _job(body, job_id="j-no-vision"),
            client=client,
            queue=MagicMock(),
        )
    )
    assert result.blocked is not None
    payload = _reply_payload(client)
    # reason stays observed gate identity (bare string).
    assert payload["reason"] == "vision_field_missing"
    # Member-4 proof: fix_hint tagged derived; constant string remains value.
    assert payload["fix_hint"]["register"] == "derived"
    assert payload["fix_hint"]["value"] == VISION_MISSING_FIX_HINT
    assert payload["fix_hint"]["basis"] == "admit_gates.vision_field_missing"


def _in_seat(answer_body: str | None) -> tuple[dict, AsyncMock]:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    result = asyncio.run(
        terminal_in_seat(
            _job("Are you live?", contract="answer", job_id="j-answer"),
            client=client,
            queue=MagicMock(),
            model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5"},
            effort={"requested": "low", "resolved_effort": "low"},
            contract_info={"contract": "answer", "disposition_hint": "answered"},
            gate_plan={"action": "dispatch_now"},
            answer_body=answer_body,
        )
    )
    return result, client


def test_answer_without_content_declines_with_routing_hint() -> None:
    result, client = _in_seat(None)
    assert result["disposition"] == "declined"
    assert result.blocked["terminal_status"] == "status:done"
    payload = _reply_payload(client)
    assert payload["disposition"] == "declined"
    assert payload["declined_reason"] == ANSWER_DECLINED_REASON
    assert "contract=implement" in payload["routing_hint"]
    assert "answer_body" not in payload


def test_answer_with_content_stays_answered() -> None:
    result, client = _in_seat("Auto is live; handler heartbeat 2s ago.")
    assert result["disposition"] == "answered"
    payload = _reply_payload(client)
    assert payload["answer_body"].startswith("Auto is live")
    assert "routing_hint" not in payload
