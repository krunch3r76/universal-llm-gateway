"""Request-surface ``hop`` verb — payload, validation, degrade, not a contract."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from contract_vocab import CANONICAL_CONTRACTS
from hop_handoff import StandingHandoffFreshness
from tools.agent_bus.hop import _hop_dispatch
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
    result = _hop_dispatch(reason="mcp-restart-healthy", from_agent="web-anthropic")
    assert result["reason"] == "hop_thread_required"


def test_hop_rejects_missing_reason() -> None:
    result = _hop_dispatch(thread="77", from_agent="web-anthropic")
    assert result["reason"] == "hop_reason_required"


def test_hop_impl_forwards_continuity_hop_and_handoff_body() -> None:
    captured: dict[str, object] = {}

    def fake_impl(**kwargs):
        captured.update(kwargs)
        return {
            "handler_status": "auto-admit-armed",
            "thread": {"id": "77"},
            "turn": {"turn_number": 2},
        }

    with (
        patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
        patch("tools.agent_bus.hop._request_impl", side_effect=fake_impl),
        patch("tools.agent_bus.hop.record"),
    ):
        result = _hop_dispatch(
            thread="77",
            reason="mcp-restart-healthy",
            from_agent="web-anthropic",
        )
    assert captured["continuity_hop"] is True
    assert captured["contract"] == "answer"
    assert captured["new_slug"] is None
    assert captured["thread"] == "77"
    body = str(captured["body"])
    first = next(line for line in body.splitlines() if line.strip())
    assert first == "TYPE: CONTINUITY_HANDOFF"
    assert "source: agent-bus-hop-verb" in body
    assert "trigger: mcp-restart-healthy" in body
    assert result["continuity_hop"] is True
    assert result["successor"]["handle"] == "execution_id"
    assert result["handler_status"] == "auto-admit-armed"
    assert "status:done" not in str(result)


def test_hop_degrades_when_auto_dead() -> None:
    with (
        patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
        patch(
            "tools.agent_bus.hop._request_impl",
            return_value={
                "handler_status": "no-auto-handler",
                "enqueue_failure": {"reason": "no_live_handler", "terminal_park": True},
                "thread": {"id": "77"},
                "turn": {"turn_number": 1},
            },
        ),
        patch("tools.agent_bus.hop.record"),
    ):
        result = _hop_dispatch(
            thread=77,
            reason="mcp-restart-healthy",
            from_agent="web-anthropic",
        )
    assert result["handler_status"] == "no-auto-handler"
    assert result["continuity_hop"] is True
    assert result["enqueue_failure"]["terminal_park"] is True


def test_enqueue_omits_continuity_hop_when_false() -> None:
    with patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b'{"ok": true}'
        resp.json.return_value = {"ok": True}
        client.post.return_value = resp
        enqueue_auto_job(
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
    payload = client.post.call_args.kwargs["json"]
    assert "continuity_hop" not in payload


def test_enqueue_includes_continuity_hop_when_true() -> None:
    with patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b'{"ok": true}'
        resp.json.return_value = {"ok": True}
        client.post.return_value = resp
        enqueue_auto_job(
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
    payload = client.post.call_args.kwargs["json"]
    assert payload["continuity_hop"] is True
