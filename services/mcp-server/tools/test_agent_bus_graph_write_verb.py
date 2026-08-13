"""Request-surface ``substrate_graph_write`` verb — validation and cortex relay."""

from __future__ import annotations

import inspect
from unittest.mock import patch

from contract_vocab import CANONICAL_CONTRACTS

from tools.agent_bus.graph_write import _graph_write_dispatch


def test_substrate_graph_write_not_in_canonical_contracts() -> None:
    assert "substrate_graph_write" not in CANONICAL_CONTRACTS


def test_graph_write_dispatch_signature_rejects_hop_fields() -> None:
    params = inspect.signature(_graph_write_dispatch).parameters
    assert "thread" not in params
    assert "continuity_hop" not in params
    assert "new_slug" not in params


def test_graph_write_rejects_missing_entity_id() -> None:
    result = _graph_write_dispatch(claim="rot observed")
    assert result["reason"] == "graph_write_entity_required"
    assert result["status_code"] == 422


def test_graph_write_rejects_missing_claim() -> None:
    result = _graph_write_dispatch(entity_id="todo:foo")
    assert result["reason"] == "graph_write_claim_required"
    assert result["status_code"] == 422


def test_graph_write_does_not_post_when_entity_missing() -> None:
    with patch("tools.agent_bus.graph_write.write_claim") as write_claim:
        _graph_write_dispatch(claim="x")
    write_claim.assert_not_called()


def test_graph_write_does_not_post_when_claim_missing() -> None:
    with patch("tools.agent_bus.graph_write.write_claim") as write_claim:
        _graph_write_dispatch(entity_id="todo:foo")
    write_claim.assert_not_called()


def test_graph_write_forwards_to_lib_and_stamps_ids() -> None:
    with patch(
        "tools.agent_bus.graph_write.write_claim",
        return_value={"item": {"id": 99}, "entity_id": "todo:foo"},
    ) as write_claim:
        result = _graph_write_dispatch(
            entity_id="todo:foo",
            claim="Substrate rot observed",
            evidence_uris=["agent-bus:77"],
        )
    write_claim.assert_called_once_with(
        entity_id="todo:foo",
        claim="Substrate rot observed",
        confidence="confirmed",
        derivation_type="direct_observation",
        evidence=None,
        evidence_uris=["agent-bus:77"],
        seat="mcp",
        via_adapter=True,
        surface="code",
    )
    assert result["assertion_id"] == 99
    assert result["entity_id"] == "todo:foo"


def test_graph_write_does_not_mint_on_404() -> None:
    with patch(
        "tools.agent_bus.graph_write.write_claim",
        return_value={
            "error": "Entity not found: todo:missing",
            "status_code": 404,
        },
    ):
        result = _graph_write_dispatch(
            entity_id="todo:missing",
            claim="Substrate rot observed",
        )
    assert result["status_code"] == 404
    assert "error" in result
    assert "mint" not in str(result).lower()
