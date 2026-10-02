"""Request-surface ``hop`` verb — payload, validation, degrade, not a contract."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any
from unittest.mock import patch

import pytest
from agent_bus_store.body_briefing_advisory import INLINE_CONTRACT_PREFIXES
from agent_bus_store.turns_models import (
    MAX_LONG_TURN_BODY_CHARS,
    MAX_TURN_BODY_CHARS,
)
from contract_vocab import CANONICAL_CONTRACTS
from hop_handoff import (
    StandingHandoffFreshness,
    build_continuity_handoff_body,
    parse_successor_birth_id,
)

from tools.agent_bus.hop import _hop_dispatch, resolve_hop_successor_model
from tools.agent_bus.request_intake import reset_request_id_registry_for_tests
from tools.agent_bus.request_worker_client import enqueue_auto_job


def setup_function() -> None:
    reset_request_id_registry_for_tests()


_HANDOFF = StandingHandoffFreshness(
    status="current",
    uri="cortex://notes/system/threads/77-standing-handoff.md",
    mtime_epoch=1.0,
    age_s=10.0,
)


def test_hop_not_in_canonical_contracts() -> None:
    assert "hop" not in CANONICAL_CONTRACTS


def test_hop_dispatch_signature_rejects_new_slug() -> None:
    assert "new_slug" not in inspect.signature(_hop_dispatch).parameters


def test_hop_rejects_missing_thread() -> None:
    result = asyncio.run(
        _hop_dispatch(reason="mcp-restart-healthy", from_agent="web-anthropic")
    )
    assert result["reason"] == "hop_thread_required"


def test_hop_rejects_missing_reason() -> None:
    result = asyncio.run(_hop_dispatch(thread="77", from_agent="web-anthropic"))
    assert result["reason"] == "hop_reason_required"


@pytest.mark.offline
@pytest.mark.parametrize(
    ("desired", "expected"),
    [
        ("", "cdp/opus-5.5-high"),
        ("auto", "cdp/opus-5.5-high"),
        ("cdp/opus-5.5-max", "cdp/opus-5.5-max"),
        ("fable-5.1-high", "cdp/fable-5.1-high"),
        ("cursor/grok-4.7", "cursor/grok-4.7"),
    ],
)
def test_resolve_hop_successor_model(desired: str, expected: str) -> None:
    """Omitted pin is opus-5.5-high; a bare family is a cdp wire id."""
    assert resolve_hop_successor_model(desired) == expected


def test_resolve_hop_successor_model_rejects_whitespace() -> None:
    with pytest.raises(ValueError):
        resolve_hop_successor_model("opus 5")


def test_hop_dispatch_sends_caller_model() -> None:
    """The generate body uses the resolved pin, not a hardcoded opus-5."""
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-1"}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
        ):
            return await _hop_dispatch(
                thread="77",
                reason="context-pressure",
                from_agent="web-anthropic",
                desired_model="fable-5.1-high",
            )

    result = asyncio.run(run())
    assert result["model"] == "cdp/fable-5.1-high"
    assert captured["body"]["model"] == "cdp/fable-5.1-high"
    assert result["continuity_hop"] is True


def test_hop_relay_body_wires_mission_kind_and_predecessor_registration() -> None:
    """Live a:37182 shape: mid-stream hop must arm hop_own_generate at Stargate.

    Without mission_kind=hop the fire gate treats the successor as a second
    operator-proxy generate and refuses with cdp_external_gate_live against the
    caller's own unsealed stream. Registration names the predecessor when the
    seated CSE supplies cse_registration_id.
    """
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-wire"}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
        ):
            return await _hop_dispatch(
                thread="12286",
                reason="context-pressure-self-handoff",
                from_agent="web-anthropic",
                desired_model="cdp/opus-5.5-extra",
                cse_registration_id="c1caf180",
            )

    result = asyncio.run(run())
    body = captured["body"]
    assert body["mission_kind"] == "hop"
    assert body["predecessor_registration_id"] == "c1caf180"
    assert body["session"] == "operator-proxy"
    assert body["parent_thread"] == "12286"
    assert body["dispatch_thread_id"] == "12286"
    assert result["continuity_hop"] is True


def test_hop_relay_body_omits_predecessor_when_registration_absent() -> None:
    """No CSE registration: still mission_kind=hop; sole-gate exempt at Stargate."""
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-sole"}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
        ):
            return await _hop_dispatch(
                thread="12286",
                reason="context-pressure",
                from_agent="web-anthropic",
            )

    asyncio.run(run())
    body = captured["body"]
    assert body["mission_kind"] == "hop"
    assert "predecessor_registration_id" not in body


def test_hop_impl_forwards_continuity_hop_and_handoff_body() -> None:
    captured: dict[str, object] = {}

    async def fake_relay(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-77"}

    async def run() -> dict[str, object]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
        ):
            return await _hop_dispatch(
                thread="77",
                reason="mcp-restart-healthy",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert captured["endpoint"] == "/api/v1/team/dispatch"
    assert captured["record_prefix"] == "mcp.agentbus.hop.dispatch"
    assert result["execution_id"] == "ex-hop-77"
    body_payload = captured["body"]
    assert body_payload["mission_kind"] == "hop"
    assert body_payload["parent_thread"] == "77"
    assert body_payload["dispatch_thread_id"] == "77"
    assert body_payload["op"] == "generate"
    assert body_payload["job"] == "freeform"
    assert body_payload["session"] == "operator-proxy"
    body = str(body_payload["prompt"])
    first = next(line for line in body.splitlines() if line.strip())
    assert first == "TYPE: CONTINUITY_HANDOFF"
    assert "source: agent-bus-hop-verb" in body
    assert "trigger: mcp-restart-healthy" in body
    assert "successor_birth_id:" in body
    assert result["continuity_hop"] is True
    assert result["successor"]["handle"] == "successor_birth_id"
    assert result["successor"]["names"] == "successor"
    assert result["successor"]["value"]
    assert "predecessor's receipt" in result["successor"]["note"]
    assert "status:done" not in str(result)


def test_hop_passes_relay_payload_through() -> None:
    async def fake_relay(**kwargs: object) -> dict[str, object]:
        del kwargs
        return {"status": "queued", "execution_id": "ex-q"}

    async def run() -> dict[str, object]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
        ):
            return await _hop_dispatch(
                thread=77,
                reason="mcp-restart-healthy",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result["status"] == "queued"
    assert result["execution_id"] == "ex-q"
    assert result["continuity_hop"] is True


def test_hop_relay_error_returns_without_continuity_hop() -> None:
    async def fake_relay(**kwargs: object) -> dict[str, str]:
        del kwargs
        return {"error": "dispatch_failed"}

    async def run() -> dict[str, object]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
        ):
            return await _hop_dispatch(
                thread="77",
                reason="mcp-restart-healthy",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result["error"] == "dispatch_failed"
    assert "continuity_hop" not in result


def test_hop_commission_refused_when_contract_job_unknown() -> None:
    async def fake_relay(**kwargs: object) -> dict[str, str]:
        raise AssertionError(f"relay must not run: {kwargs!r}")

    async def run() -> dict[str, object]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
            patch(
                "tools.agent_bus.hop.build_continuity_handoff_body",
                return_value="TYPE: CONTINUITY_HANDOFF\ncontract: none\n",
            ),
        ):
            return await _hop_dispatch(
                thread="77",
                reason="mcp-restart-healthy",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result["posted"] is False
    assert result["reason"] == "job_unknown"
    assert result["continuity_hop"] is True


def test_enqueue_omits_continuity_hop_when_false() -> None:
    result = enqueue_auto_job(
        thread_id="77",
        turn_number=1,
        subject="s",
        body="b",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="answer",
    )
    assert result["reason"] == "auto_arm_removed"
    assert result["continuity_hop"] is False


def test_enqueue_includes_continuity_hop_when_true() -> None:
    result = enqueue_auto_job(
        thread_id="77",
        turn_number=1,
        subject="s",
        body="TYPE: CONTINUITY_HANDOFF\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="answer",
        continuity_hop=True,
    )
    assert result["reason"] == "auto_arm_removed"
    assert result["continuity_hop"] is True


@pytest.mark.parametrize(
    "reason",
    ["r" * 38, "r" * 3000, "r" * (MAX_TURN_BODY_CHARS + 1)],
)
def test_hop_relays_full_handoff_prompt(reason: str) -> None:
    """Hop relays the full structural handoff prompt (no bus enqueue path)."""
    handoff = StandingHandoffFreshness(
        status="current",
        uri="cortex://notes/system/threads/12286-standing-handoff.md",
        mtime_epoch=1.0,
        age_s=10.0,
    )
    specimen = build_continuity_handoff_body(
        thread_id="12286",
        trigger=reason,
        source="agent-bus-hop-verb",
        handoff=handoff,
    )
    if len(reason) > 2000:
        assert len(specimen) > 2000, len(specimen)
    if len(reason) > MAX_TURN_BODY_CHARS:
        assert MAX_TURN_BODY_CHARS < len(specimen) <= MAX_LONG_TURN_BODY_CHARS, len(
            specimen
        )

    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-long"}

    async def run() -> dict[str, Any]:
        with (
            patch(
                "tools.agent_bus.hop.assess_standing_handoff",
                return_value=handoff,
            ),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
        ):
            return await _hop_dispatch(
                thread="12286",
                reason=reason,
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    prompt = str(captured["body"]["prompt"])
    assert prompt.splitlines()[0] == "TYPE: CONTINUITY_HANDOFF"
    assert "Sidecar:" not in prompt
    birth = parse_successor_birth_id(prompt)
    assert prompt == build_continuity_handoff_body(
        thread_id="12286",
        trigger=reason,
        source="agent-bus-hop-verb",
        handoff=handoff,
        successor_birth_id=birth,
    )
    assert "TYPE: CONTINUITY_HANDOFF" not in INLINE_CONTRACT_PREFIXES


def test_hop_relays_prompt_past_long_turn_cap() -> None:
    """Past 64k the hop still builds the handoff and relays it (no bus spill)."""
    reason = "r" * (MAX_LONG_TURN_BODY_CHARS + 1)
    handoff = StandingHandoffFreshness(
        status="current",
        uri="cortex://notes/system/threads/12286-standing-handoff.md",
        mtime_epoch=1.0,
        age_s=10.0,
    )
    specimen = build_continuity_handoff_body(
        thread_id="12286",
        trigger=reason,
        source="agent-bus-hop-verb",
        handoff=handoff,
    )
    assert len(specimen) > MAX_LONG_TURN_BODY_CHARS, len(specimen)

    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-hard"}

    async def run() -> dict[str, Any]:
        with (
            patch(
                "tools.agent_bus.hop.assess_standing_handoff",
                return_value=handoff,
            ),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
        ):
            return await _hop_dispatch(
                thread="12286",
                reason=reason,
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    assert len(str(captured["body"]["prompt"])) > MAX_LONG_TURN_BODY_CHARS
