"""Request-surface ``hop`` verb — payload, validation, degrade, not a contract."""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
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
    ArcHubTip,
    StandingHandoffFreshness,
    build_continuity_handoff_body,
    parse_successor_birth_id,
)
from hop_handoff.body import parse_hop_admitted_at, parse_responsibility

from claude_bundles.operator_proxy_mission import ensure_operator_proxy_mission_prompt

from tools.agent_bus.hop import (
    _hop_dispatch,
    hop_generate_payload,
    resolve_hop_cdp_caller_agent,
    resolve_hop_successor_model,
)
from tools.agent_bus.request_intake import reset_request_id_registry_for_tests


def setup_function() -> None:
    reset_request_id_registry_for_tests()


def _patch_lane_tags(tags: list[str] | None) -> patch:
    return patch(
        "agent_bus_store.db.threads.get_thread",
        return_value={"tags": tags or []},
    )


def _patch_no_consumption_window() -> patch:
    return patch("tools.agent_bus.pickup_gate.load_prior_turns", return_value=[])


def _noop_fetch(_lane: str) -> list:
    return []


def _admit_and_responsibility_lines_after_title(paste: str) -> tuple[str, str]:
    """Return paste lines starting with Hop admitted / RESPONSIBILITY after the title.

    Lines are located by prefix after the first ``# Hop on agent-bus:`` line, not
    by fixed indices, so a multi-line title does not break the assertion.
    """
    lines = paste.splitlines()
    title_idx = next(
        i for i, line in enumerate(lines) if line.startswith("# Hop on agent-bus:")
    )
    admitted: str | None = None
    responsibility: str | None = None
    for line in lines[title_idx + 1 :]:
        if admitted is None and line.startswith("Hop admitted:"):
            admitted = line
        elif responsibility is None and line.startswith("RESPONSIBILITY:"):
            responsibility = line
        if admitted is not None and responsibility is not None:
            break
    assert admitted is not None, paste[:500]
    assert responsibility is not None, paste[:500]
    return admitted, responsibility


_HANDOFF = StandingHandoffFreshness(
    status="current",
    uri="cortex://notes/system/threads/77-standing-handoff.md",
    mtime_epoch=1.0,
    age_s=10.0,
)

# Key set captured 2026-11-10 via MCP agent_bus thread_get(thread=22824) wire response.
_THREAD_RELAY_DETAIL_SHAPE: dict[str, Any] = {
    "id": "22824",
    "slug": "houseless-hop-seat-record",
    "status": "active",
    "summary": (
        "SUPERVISOR_MAILBOX for houseless instance:bafb3df1-8ecc-4e78-8e89-8235f5d4a049 "
        "(work slug houseless-hop-seat-record).\n"
        "RESPONSIBILITY: close the harness gap that lets a houseless instance's records "
        "of its current seat disagree after hops."
    ),
    "turn_count": 41,
    "unread_count": 19,
    "unread_basis": {
        "basis": "read_at_null",
        "recipient": None,
        "includes_superseded": False,
        "as_of": "2026-11-10T16:45:59Z",
        "source": "agent_bus_store.threads",
    },
    "last_subject": "example",
    "last_turn_from": "houseless-hop-seat-record",
    "last_turn_to": "kaywan",
    "tags": ["houseless", "mailbox", "project:ulg"],
    "created_at": "2026-11-10T14:42:39Z",
    "updated_at": "2026-11-10T16:45:43Z",
    "bus_lifecycle_state": None,
    "parent_thread": None,
    "lane_role": None,
    "cse_chat_url": "https://claude.ai/cowork/cse_01AroaPeQV3XG316CHE7Ju4B",
    "cse_registration_id": "9c54ef1b13294e16a06065be090a146b",
    "resume_envelope": None,
    "pointer_turn": None,
}


def _thread_relay_side_effect(
    thread_id: str, *, detail: dict[str, Any] | None = None
) -> Any:
    """Build a side_effect for tools.agent_bus._shared.relay thread GET."""

    def _relay(service: str, method: str, path: str, **kwargs: Any) -> Any:
        del kwargs
        if service == "agent-bus" and method == "GET" and path.startswith("/threads/"):
            assert f"/threads/{thread_id}?" in path
            assert "include_resume=false" in path
            if detail is None:
                raise RuntimeError("thread relay failed")
            return dict(detail)
        raise AssertionError(f"unexpected agent-bus relay: {service} {method} {path!r}")

    return _relay


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


def test_hop_cdp_caller_from_web_anthropic_uses_cursor_mailbox() -> None:
    assert resolve_hop_cdp_caller_agent("web-anthropic") == "cursor"


def test_hop_generate_payload_explicit_caller_passthrough() -> None:
    decision = {
        "post": True,
        "job": "freeform",
        "reason": None,
        "report_job": "freeform",
    }
    payload = hop_generate_payload(
        model="cdp/opus-5.5",
        prompt="TYPE: CONTINUITY_HANDOFF",
        thread_id="77",
        from_agent="web-anthropic",
        decision=decision,
        caller_agent="cursor-sdk-lane",
    )
    assert payload["caller_agent"] == "cursor-sdk-lane"


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
    assert captured["body"]["caller_agent"] == "cursor"
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


def test_hop_relay_mission_kind_with_hop_kind_housekeeping() -> None:
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hk"}

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
                reason="housekeeping: budget_exceeded",
                from_agent="web-anthropic",
                hop_kind="housekeeping",
            )

    asyncio.run(run())
    assert captured["body"]["mission_kind"] == "hop"
    assert "hop_kind: housekeeping" in captured["body"]["prompt"]


def test_hop_relay_mission_kind_when_hop_kind_omitted() -> None:
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-mission-default"}

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
    assert captured["body"]["mission_kind"] == "hop"
    assert "hop_kind:" not in captured["body"]["prompt"]


def test_hop_rejects_invalid_hop_kind_before_relay() -> None:
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
        ):
            return await _hop_dispatch(
                thread="12286",
                reason="housekeeping",
                from_agent="web-anthropic",
                hop_kind="bogus",
            )

    result = asyncio.run(run())
    assert result["reason"] == "hop_kind_invalid"


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


@pytest.mark.parametrize(
    "reason",
    ["r" * 38, "r" * 3000, "r" * (MAX_TURN_BODY_CHARS + 1)],
)
def test_hop_relays_full_handoff_prompt(reason: str, tmp_path) -> None:
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
            patch(
                "hop_handoff.standing_handoff.standing_handoff_path",
                return_value=tmp_path / "missing-standing-handoff.md",
            ),
            _patch_lane_tags([]),
            _patch_no_consumption_window(),
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
        standing_handoff_budget=handoff.budget,
        standing_handoff_identity=handoff.identity,
        inflight_rows=None,
        arc_hub=None,
        hop_admitted_at=parse_hop_admitted_at(prompt),
        responsibility=parse_responsibility(prompt),
    )
    assert "TYPE: CONTINUITY_HANDOFF" not in INLINE_CONTRACT_PREFIXES


def test_hop_emits_inflight_rows_when_sidecar_has_in_flight(tmp_path) -> None:
    """Sidecar IN FLIGHT rows become ``inflight_rows: N`` on the handoff body."""
    sidecar = (
        "## CURRENT LEG\n"
        "### IN FLIGHT\n"
        '- todo:a · worker 111 · poll_hint {"thread":"12286"}\n'
        '- todo:b · worker 222 · poll_hint {"thread":"12286"}\n'
        "### OWED / NEXT\nok\n"
    )
    sidecar_file = tmp_path / "12286-standing-handoff.md"
    sidecar_file.write_text(sidecar, encoding="utf-8")
    handoff = StandingHandoffFreshness(
        status="current",
        uri="cortex://notes/system/threads/12286-standing-handoff.md",
        mtime_epoch=1.0,
        age_s=10.0,
    )
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-inflight"}

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
            patch(
                "hop_handoff.standing_handoff.standing_handoff_path",
                return_value=sidecar_file,
            ),
            _patch_lane_tags([]),
        ):
            return await _hop_dispatch(
                thread="12286",
                reason="relay-inflight-count",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    prompt = str(captured["body"]["prompt"])
    assert "inflight_rows: 2" in prompt


def test_hop_emits_arc_hub_pair_when_lane_tagged() -> None:
    hub = ArcHubTip(
        entity_id="decision:ulg-arc-standing-terms",
        tip="a:99@2026-11-01T00:00:00Z",
        status="current",
    )
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-arc-hub"}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
            _patch_lane_tags(["arc_hub:decision:ulg-arc-standing-terms"]),
            patch("hop_handoff.arc_hub.assess_arc_hub", return_value=hub),
        ):
            return await _hop_dispatch(
                thread="21744",
                reason="arc-hub-bind",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    prompt = str(captured["body"]["prompt"])
    assert "arc_hub: decision:ulg-arc-standing-terms" in prompt
    assert "arc_hub_tip: a:99@2026-11-01T00:00:00Z" in prompt


def test_hop_emits_consumption_missing_when_window_has_unconsumed() -> None:
    window = [
        {"turn_number": 10, "body": "TYPE: RULING\nrule: open\n"},
    ]
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-consume"}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
            patch(
                "tools.agent_bus.pickup_gate.load_prior_turns",
                return_value=window,
            ),
        ):
            return await _hop_dispatch(
                thread="21744",
                reason="consumption-missing-bind",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    prompt = str(captured["body"]["prompt"])
    assert "consumption_missing: CONSUMPTION_MISSING:10" in prompt


def test_hop_omits_consumption_missing_when_window_unavailable() -> None:
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-no-consume"}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
            patch("tools.agent_bus.pickup_gate.load_prior_turns", return_value=None),
        ):
            return await _hop_dispatch(
                thread="21744",
                reason="no-consumption-window",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    prompt = str(captured["body"]["prompt"])
    assert "consumption_missing:" not in prompt


def test_hop_omits_consumption_missing_when_load_raises() -> None:
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-consume-err"}

    def boom(_thread: str) -> None:
        raise RuntimeError("store down")

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
            patch("tools.agent_bus.pickup_gate.load_prior_turns", side_effect=boom),
        ):
            return await _hop_dispatch(
                thread="21744",
                reason="consumption-load-fail",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    prompt = str(captured["body"]["prompt"])
    assert "consumption_missing:" not in prompt


def test_hop_omits_arc_hub_when_lane_untagged() -> None:
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-no-arc"}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
            _patch_lane_tags(["project:ulg"]),
        ):
            return await _hop_dispatch(
                thread="21744",
                reason="no-arc-hub",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    prompt = str(captured["body"]["prompt"])
    assert "arc_hub:" not in prompt
    assert "arc_hub_tip:" not in prompt


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


def test_hop_body_carries_admit_stamp_and_responsibility_from_summary() -> None:
    """Thread summary RESPONSIBILITY line and one admit stamp land on the handoff."""
    from datetime import UTC, datetime

    captured: dict[str, Any] = {}
    fixed = datetime(2026, 3, 15, 12, 34, 56, tzinfo=UTC)

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-meta"}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
            patch(
                "tools.agent_bus._shared.relay",
                side_effect=_thread_relay_side_effect(
                    "77",
                    detail={
                        **_THREAD_RELAY_DETAIL_SHAPE,
                        "id": "77",
                        "summary": "lane arc\nRESPONSIBILITY: hold the operator seat",
                        "tags": [],
                    },
                ),
            ),
            patch("tools.agent_bus.hop.datetime") as dt_mock,
            _patch_no_consumption_window(),
        ):
            dt_mock.UTC = UTC
            dt_mock.now.return_value = fixed
            return await _hop_dispatch(
                thread="77",
                reason="stamp-responsibility",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    prompt = str(captured["body"]["prompt"])
    assert "hop_admitted_at: 2026-03-15T12:34:56Z" in prompt
    assert "responsibility: hold the operator seat" in prompt


@pytest.mark.offline
def test_hop_verbal_stamp_renders_on_opening_paste() -> None:
    """Handoff the hop verb stamped must match Hop admitted / RESPONSIBILITY on paste."""
    from datetime import UTC, datetime

    captured: dict[str, Any] = {}
    fixed = datetime(2026, 3, 15, 12, 34, 56, tzinfo=UTC)

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-paste-stamp"}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
            patch(
                "tools.agent_bus._shared.relay",
                side_effect=_thread_relay_side_effect(
                    "77",
                    detail={
                        **_THREAD_RELAY_DETAIL_SHAPE,
                        "id": "77",
                        "summary": "lane arc\nRESPONSIBILITY: hold the operator seat",
                        "tags": [],
                    },
                ),
            ),
            patch("tools.agent_bus.hop.datetime") as dt_mock,
            _patch_no_consumption_window(),
        ):
            dt_mock.UTC = UTC
            dt_mock.now.return_value = fixed
            return await _hop_dispatch(
                thread="77",
                reason="stamp-responsibility-paste",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    handoff_prompt = str(captured["body"]["prompt"])
    paste = ensure_operator_proxy_mission_prompt(
        handoff_prompt,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    admitted, responsibility = _admit_and_responsibility_lines_after_title(paste)
    assert admitted == "Hop admitted: 2026-03-15T12:34:56Z"
    assert responsibility == "RESPONSIBILITY: hold the operator seat"


def test_hop_admits_when_thread_read_raises() -> None:
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-no-thread"}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
            patch(
                "tools.agent_bus._shared.relay",
                side_effect=_thread_relay_side_effect("77", detail=None),
            ),
            _patch_no_consumption_window(),
        ):
            return await _hop_dispatch(
                thread="77",
                reason="thread-read-fail",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    prompt = str(captured["body"]["prompt"])
    assert "hop_admitted_at:" in prompt
    assert "responsibility:" not in prompt


@pytest.mark.offline
def test_hop_thread_read_fail_responsibility_not_recorded_on_paste() -> None:
    """When the thread relay fails, paste still shows hop time and not-recorded responsibility."""
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-paste-raises"}

    record_mock_holder: dict[str, Any] = {}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record") as record_mock,
            patch("tools.frontier._relay", side_effect=fake_relay),
            patch(
                "tools.agent_bus._shared.relay",
                side_effect=_thread_relay_side_effect("77", detail=None),
            ),
            _patch_no_consumption_window(),
        ):
            record_mock_holder["mock"] = record_mock
            return await _hop_dispatch(
                thread="77",
                reason="thread-read-fail-paste",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    handoff_prompt = str(captured["body"]["prompt"])
    paste = ensure_operator_proxy_mission_prompt(
        handoff_prompt,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    admitted, responsibility = _admit_and_responsibility_lines_after_title(paste)
    assert responsibility == "RESPONSIBILITY: not recorded"
    assert admitted.startswith("Hop admitted: ")
    assert admitted.endswith("Z")
    assert admitted != "Hop admitted: not recorded"
    record_mock = record_mock_holder["mock"]
    failed = [
        c
        for c in record_mock.call_args_list
        if c.args and c.args[0] == "mcp.agentbus.hop.thread_read_failed"
    ]
    assert len(failed) == 1


def test_hop_null_summary_omits_responsibility_line() -> None:
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-null-summary"}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
            patch(
                "tools.agent_bus._shared.relay",
                side_effect=_thread_relay_side_effect(
                    "77",
                    detail={
                        **_THREAD_RELAY_DETAIL_SHAPE,
                        "id": "77",
                        "summary": None,
                        "tags": [],
                    },
                ),
            ),
            _patch_no_consumption_window(),
        ):
            return await _hop_dispatch(
                thread="77",
                reason="null-summary",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    prompt = str(captured["body"]["prompt"])
    assert "responsibility:" not in prompt


@pytest.mark.offline
def test_hop_responsibility_from_relay_when_sqlite_get_thread_fails() -> None:
    """Thread metadata relay supplies RESPONSIBILITY even when sqlite get_thread is dead."""
    from datetime import UTC, datetime

    captured: dict[str, Any] = {}
    fixed = datetime(2026, 3, 15, 12, 34, 56, tzinfo=UTC)

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-relay-resp"}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record"),
            patch("tools.frontier._relay", side_effect=fake_relay),
            patch(
                "tools.agent_bus._shared.relay",
                side_effect=_thread_relay_side_effect(
                    "22824",
                    detail=_THREAD_RELAY_DETAIL_SHAPE,
                ),
            ),
            patch(
                "agent_bus_store.db.threads.get_thread",
                side_effect=sqlite3.OperationalError("unable to open database file"),
            ),
            patch("tools.agent_bus.hop.datetime") as dt_mock,
            _patch_no_consumption_window(),
        ):
            dt_mock.UTC = UTC
            dt_mock.now.return_value = fixed
            return await _hop_dispatch(
                thread="22824",
                reason="relay-responsibility-sqlite-dead",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    handoff_prompt = str(captured["body"]["prompt"])
    paste = ensure_operator_proxy_mission_prompt(
        handoff_prompt,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    _admitted, responsibility = _admit_and_responsibility_lines_after_title(paste)
    assert responsibility.startswith("RESPONSIBILITY: close the harness gap")


@pytest.mark.offline
def test_hop_relay_raises_records_thread_read_failed_event() -> None:
    """Relay failure admits the hop, not-recorded paste, and one failure event."""
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-relay-raises-event"}

    def _raise_relay(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise TimeoutError("agent-bus slow")

    record_mock_holder: dict[str, Any] = {}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record") as record_mock,
            patch("tools.frontier._relay", side_effect=fake_relay),
            patch("tools.agent_bus._shared.relay", side_effect=_raise_relay),
            _patch_no_consumption_window(),
        ):
            record_mock_holder["mock"] = record_mock
            return await _hop_dispatch(
                thread="77",
                reason="relay-raises-event",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    handoff_prompt = str(captured["body"]["prompt"])
    paste = ensure_operator_proxy_mission_prompt(
        handoff_prompt,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    _admitted, responsibility = _admit_and_responsibility_lines_after_title(paste)
    assert responsibility == "RESPONSIBILITY: not recorded"
    record_mock = record_mock_holder["mock"]
    failed = [
        c
        for c in record_mock.call_args_list
        if c.args and c.args[0] == "mcp.agentbus.hop.thread_read_failed"
    ]
    assert len(failed) == 1
    assert failed[0].kwargs.get("exception_class") == "TimeoutError"
    assert failed[0].kwargs.get("thread") == "77"


@pytest.mark.offline
def test_hop_relay_error_dict_records_thread_read_failed_on_paste() -> None:
    """Relay error envelopes (no exception) admit, not-recorded paste, one failure event."""
    captured: dict[str, Any] = {}

    async def fake_relay(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"execution_id": "ex-hop-relay-error-dict"}

    def _error_relay(
        service: str, method: str, path: str, **kwargs: Any
    ) -> dict[str, str]:
        del service, method, path, kwargs
        return {"error": "Connection failed to agent-bus"}

    record_mock_holder: dict[str, Any] = {}

    async def run() -> dict[str, Any]:
        with (
            patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
            patch(
                "tools.agent_bus.hop._resolve_hop_seat_request_refusal",
                return_value=None,
            ),
            patch("tools.agent_bus.hop.record") as record_mock,
            patch("tools.frontier._relay", side_effect=fake_relay),
            patch("tools.agent_bus._shared.relay", side_effect=_error_relay),
            _patch_no_consumption_window(),
        ):
            record_mock_holder["mock"] = record_mock
            return await _hop_dispatch(
                thread="77",
                reason="relay-error-dict",
                from_agent="web-anthropic",
            )

    result = asyncio.run(run())
    assert result.get("continuity_hop") is True, result
    handoff_prompt = str(captured["body"]["prompt"])
    paste = ensure_operator_proxy_mission_prompt(
        handoff_prompt,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    _admitted, responsibility = _admit_and_responsibility_lines_after_title(paste)
    assert responsibility == "RESPONSIBILITY: not recorded"
    record_mock = record_mock_holder["mock"]
    failed = [
        c
        for c in record_mock.call_args_list
        if c.args and c.args[0] == "mcp.agentbus.hop.thread_read_failed"
    ]
    assert len(failed) == 1
    assert failed[0].kwargs.get("exception_class") == "RuntimeError"
    assert failed[0].kwargs.get("thread") == "77"
