"""Request-surface ``substrate_friction_file`` verb — validation and cortex relay."""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from contract_vocab import CANONICAL_CONTRACTS
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import agent_bus as agent_bus_module
from tools.agent_bus import register_agent_bus_tools
from tools.agent_bus._arg_rewrite import reconcile_dispatch_arguments
from tools.agent_bus.friction_file import _friction_file_dispatch


@pytest.fixture
def agent_bus_fn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_bus_module, "record", lambda *a, **k: None)
    mcp = FastMCP("test-friction-file-agent-bus")
    register_agent_bus_tools(mcp)
    tools = asyncio.run(mcp.list_tools())
    return next(t for t in tools if t.name == "agent_bus").fn


def test_substrate_friction_file_not_in_canonical_contracts() -> None:
    assert "substrate_friction_file" not in CANONICAL_CONTRACTS


def test_friction_file_dispatch_signature_rejects_hop_fields() -> None:
    params = inspect.signature(_friction_file_dispatch).parameters
    assert "thread" not in params
    assert "continuity_hop" not in params
    assert "new_slug" not in params


def test_friction_file_rejects_missing_owner() -> None:
    result = _friction_file_dispatch(note="enum lagged")
    assert result["reason"] == "friction_file_owner_required"
    assert result["status_code"] == 422


def test_friction_file_rejects_missing_note() -> None:
    result = _friction_file_dispatch(owner="service:mcp-server")
    assert result["reason"] == "friction_file_note_required"
    assert result["status_code"] == 422


def test_friction_file_does_not_post_when_owner_missing() -> None:
    with patch("tools.agent_bus.friction_file.file_friction") as filed:
        _friction_file_dispatch(note="x")
    filed.assert_not_called()


def test_friction_file_does_not_post_when_note_missing() -> None:
    with patch("tools.agent_bus.friction_file.file_friction") as filed:
        _friction_file_dispatch(owner="service:mcp-server")
    filed.assert_not_called()


def test_friction_file_accepts_service_and_claim_aliases() -> None:
    with patch(
        "tools.agent_bus.friction_file.file_friction",
        return_value={"item": {"id": 77}},
    ) as filed:
        result = _friction_file_dispatch(
            service="mcp-server",
            claim="enum lagged",
            category="tool_error",
        )
    filed.assert_called_once_with(
        owner="mcp-server",
        note="enum lagged",
        category="tool_error",
        suggestion=None,
        evidence_uris=None,
        confidence=None,
        agent=None,
        actionable=None,
        actionable_false_reason=None,
        seat="mcp",
        caller="mcp-server",
        surface="code",
    )
    assert result["assertion_id"] == 77
    assert result["owner"] == "mcp-server"


def test_friction_file_rejects_conflicting_note_and_claim() -> None:
    with patch("tools.agent_bus.friction_file.file_friction") as filed:
        result = _friction_file_dispatch(
            owner="service:mcp-server",
            note="wrapper note",
            claim="substantive finding",
        )
    filed.assert_not_called()
    assert result["reason"] == "friction_file_note_claim_conflict"
    assert result["status_code"] == 422


def test_friction_file_does_not_mint_on_404() -> None:
    with patch(
        "tools.agent_bus.friction_file.file_friction",
        return_value={
            "error": "Entity not found: service:missing",
            "status_code": 404,
        },
    ):
        result = _friction_file_dispatch(
            owner="service:missing",
            note="enum lagged",
        )
    assert result["status_code"] == 404
    assert "error" in result
    assert "mint" not in str(result).lower()


def test_friction_file_forwards_protocol_actionable() -> None:
    with patch(
        "tools.agent_bus.friction_file.file_friction",
        return_value={"item": {"id": 9}},
    ) as filed:
        _friction_file_dispatch(
            owner="service:mcp-server",
            note="unanchored protocol",
            category="protocol",
            actionable=False,
            actionable_false_reason="filed via unanchored substrate_friction_file",
        )
    assert filed.call_args.kwargs["category"] == "protocol"
    assert filed.call_args.kwargs["actionable"] is False


def test_friction_file_rewrite_from_agent_alias() -> None:
    parsed, err, advisories = reconcile_dispatch_arguments(
        "substrate_friction_file",
        {
            "owner": "service:mcp-server",
            "note": "enum lagged",
            "from_agent": "cursor-sdk",
        },
    )
    assert err is None
    assert parsed["agent"] == "cursor-sdk"
    assert "from_agent" not in parsed
    assert any("remapped" in a for a in advisories)


def test_friction_file_rewrite_from_alias() -> None:
    parsed, err, advisories = reconcile_dispatch_arguments(
        "substrate_friction_file",
        {
            "owner": "service:mcp-server",
            "note": "enum lagged",
            "from": "web-anthropic",
        },
    )
    assert err is None
    assert parsed["agent"] == "web-anthropic"
    assert "from" not in parsed
    assert advisories


def test_friction_file_rewrite_rejects_agent_alias_conflict() -> None:
    _, err, _ = reconcile_dispatch_arguments(
        "substrate_friction_file",
        {
            "owner": "service:mcp-server",
            "note": "enum lagged",
            "agent": "cursor",
            "from_agent": "grok-bot",
        },
    )
    assert err is not None
    assert err["reason"] == "friction_file_agent_conflict"


def test_friction_file_rewrite_rejects_from_from_agent_conflict() -> None:
    _, err, _ = reconcile_dispatch_arguments(
        "substrate_friction_file",
        {
            "owner": "service:mcp-server",
            "note": "enum lagged",
            "from": "cursor",
            "from_agent": "grok-bot",
        },
    )
    assert err is not None
    assert err["reason"] == "friction_file_agent_alias_conflict"


def test_friction_file_dispatch_accepts_from_agent_via_agent_bus(agent_bus_fn) -> None:
    with patch(
        "tools.agent_bus.friction_file.file_friction",
        return_value={"item": {"id": 42}},
    ) as filed:
        result = asyncio.run(
            agent_bus_fn(
                tool="substrate_friction_file",
                arguments=json.dumps(
                    {
                        "owner": "service:mcp-server",
                        "note": "ingress from_agent alias",
                        "from_agent": "cursor-sdk",
                    }
                ),
            )
        )
    assert "error" not in result
    assert filed.call_args.kwargs["agent"] == "cursor-sdk"


def test_friction_file_agent_bus_rejects_from_and_from_agent_conflict(
    agent_bus_fn,
) -> None:
    with patch("tools.agent_bus.friction_file.file_friction") as filed:
        result = asyncio.run(
            agent_bus_fn(
                tool="substrate_friction_file",
                arguments=json.dumps(
                    {
                        "owner": "service:mcp-server",
                        "note": "alias conflict ingress",
                        "from": "cursor",
                        "from_agent": "grok-bot",
                    }
                ),
            )
        )
    assert filed.call_count == 0
    assert "error" in result
    assert result["reason"] == "friction_file_agent_alias_conflict"


def test_friction_file_agent_bus_rejects_agent_and_from_agent_conflict(
    agent_bus_fn,
) -> None:
    with patch("tools.agent_bus.friction_file.file_friction") as filed:
        result = asyncio.run(
            agent_bus_fn(
                tool="substrate_friction_file",
                arguments=json.dumps(
                    {
                        "owner": "service:mcp-server",
                        "note": "alias conflict ingress",
                        "agent": "cursor",
                        "from_agent": "grok-bot",
                    }
                ),
            )
        )
    assert filed.call_count == 0
    assert "error" in result
    assert result["reason"] == "friction_file_agent_conflict"
