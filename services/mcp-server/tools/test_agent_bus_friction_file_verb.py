"""Request-surface ``substrate_friction_file`` verb — validation and cortex relay."""

from __future__ import annotations

import inspect
from unittest.mock import patch

from contract_vocab import CANONICAL_CONTRACTS

from tools.agent_bus.friction_file import _friction_file_dispatch


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
        seat="mcp",
        via_adapter=True,
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
