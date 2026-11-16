"""A3 regressions: ``analyze_impact`` confidence default vs explicit / invalid."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops.ops_assertions import _op_analyze_impact
from cortex_store.models import ImpactAnalysisRequest


@pytest.mark.offline
@patch("cortex_store.dispatch_ops.ops_assertions.analyze_impact_semantic")
@patch("cortex_store.dispatch_ops.ops_assertions.cortex_conn")
@patch("cortex_store.dispatch_ops.ops_assertions.resolve_entity_reference")
def test_analyze_impact_omitted_confidence_uses_model_default(
    mock_resolve,
    mock_conn,
    mock_semantic,
) -> None:
    mock_resolve.return_value.entity_id = "service:mcp-server"
    mock_conn.return_value.__enter__ = lambda *_: None  # type: ignore[method-assign]
    mock_conn.return_value.__exit__ = lambda *_: None  # type: ignore[method-assign]
    mock_semantic.return_value.model_dump.return_value = {"impact_score": 0.0}

    result = _op_analyze_impact(
        entity_id="service:mcp-server",
        claim="test claim for default confidence",
        confidence=None,
    )
    assert "error" not in result, result
    req = mock_semantic.call_args[0][0]
    assert isinstance(req, ImpactAnalysisRequest)
    assert req.confidence == "believed"


@pytest.mark.offline
@patch("cortex_store.dispatch_ops.ops_assertions.analyze_impact_semantic")
@patch("cortex_store.dispatch_ops.ops_assertions.cortex_conn")
@patch("cortex_store.dispatch_ops.ops_assertions.resolve_entity_reference")
def test_analyze_impact_explicit_confidence_forwarded(
    mock_resolve,
    mock_conn,
    mock_semantic,
) -> None:
    mock_resolve.return_value.entity_id = "service:mcp-server"
    mock_conn.return_value.__enter__ = lambda *_: None  # type: ignore[method-assign]
    mock_conn.return_value.__exit__ = lambda *_: None  # type: ignore[method-assign]
    mock_semantic.return_value.model_dump.return_value = {"impact_score": 0.0}

    result = _op_analyze_impact(
        entity_id="service:mcp-server",
        claim="test claim for confirmed confidence",
        confidence="confirmed",
    )
    assert "error" not in result, result
    req = mock_semantic.call_args[0][0]
    assert req.confidence == "confirmed"


@pytest.mark.offline
@patch("cortex_store.dispatch_ops.ops_assertions.cortex_conn")
@patch("cortex_store.dispatch_ops.ops_assertions.resolve_entity_reference")
def test_analyze_impact_invalid_confidence_returns_422(
    mock_resolve,
    mock_conn,
) -> None:
    mock_resolve.return_value.entity_id = "service:mcp-server"
    mock_conn.return_value.__enter__ = lambda *_: None  # type: ignore[method-assign]
    mock_conn.return_value.__exit__ = lambda *_: None  # type: ignore[method-assign]

    result = _op_analyze_impact(
        entity_id="service:mcp-server",
        claim="test claim",
        confidence="not_a_valid_band",
    )
    assert "error" in result
    assert "Invalid confidence" in result["error"]
