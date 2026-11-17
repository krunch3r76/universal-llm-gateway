"""A3 regressions: ``analyze_impact`` confidence default vs explicit / invalid."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops.ops_assertions import _op_analyze_impact
from cortex_store.models import ImpactAnalysisRequest


@pytest.fixture()
def analyze_impact_db(migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    with cortex_db.cortex_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'service', ?)",
            ("service:mcp-server", "mcp-server"),
        )
        conn.commit()
    return migrated_db_path


def _mock_impact_graph(mock_resolve, mock_conn, mock_semantic) -> None:
    mock_resolve.return_value.entity_id = "service:mcp-server"
    mock_conn.return_value.__enter__ = lambda *_: None  # type: ignore[method-assign]
    mock_conn.return_value.__exit__ = lambda *_: None  # type: ignore[method-assign]
    mock_semantic.return_value.model_dump.return_value = {"impact_score": 0.0}


@pytest.mark.offline
@patch("cortex_store.dispatch_ops.ops_assertions.analyze_impact_semantic")
@patch("cortex_store.dispatch_ops.ops_assertions.cortex_conn")
@patch("cortex_store.dispatch_ops.ops_assertions.resolve_entity_reference")
def test_analyze_impact_omitted_confidence_uses_model_default(
    mock_resolve,
    mock_conn,
    mock_semantic,
) -> None:
    _mock_impact_graph(mock_resolve, mock_conn, mock_semantic)

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
    _mock_impact_graph(mock_resolve, mock_conn, mock_semantic)

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


@pytest.mark.offline
@patch("cortex_store.dispatch_ops.ops_assertions.analyze_impact_semantic")
@patch("cortex_store.dispatch_ops.ops_assertions.cortex_conn")
@patch("cortex_store.dispatch_ops.ops_assertions.resolve_entity_reference")
def test_execute_op_analyze_impact_omitted_confidence_defaults_believed(
    mock_resolve,
    mock_conn,
    mock_semantic,
) -> None:
    _mock_impact_graph(mock_resolve, mock_conn, mock_semantic)

    result = execute_op(
        "analyze_impact",
        {
            "entity_id": "service:mcp-server",
            "claim": "public dispatch path default confidence",
        },
    )
    assert "error" not in result, result
    req = mock_semantic.call_args[0][0]
    assert isinstance(req, ImpactAnalysisRequest)
    assert req.confidence == "believed"


@pytest.mark.offline
@patch("cortex_store.dispatch_ops.ops_assertions.analyze_impact_semantic")
@patch("cortex_store.dispatch_ops.ops_assertions.cortex_conn")
@patch("cortex_store.dispatch_ops.ops_assertions.resolve_entity_reference")
def test_execute_op_analyze_impact_explicit_confidence_forwarded(
    mock_resolve,
    mock_conn,
    mock_semantic,
) -> None:
    _mock_impact_graph(mock_resolve, mock_conn, mock_semantic)

    result = execute_op(
        "analyze_impact",
        {
            "entity_id": "service:mcp-server",
            "claim": "public dispatch path explicit confidence",
            "confidence": "confirmed",
        },
    )
    assert "error" not in result, result
    req = mock_semantic.call_args[0][0]
    assert req.confidence == "confirmed"


@pytest.mark.offline
@patch("cortex_store.dispatch_ops.ops_assertions.cortex_conn")
@patch("cortex_store.dispatch_ops.ops_assertions.resolve_entity_reference")
def test_execute_op_analyze_impact_invalid_confidence_returns_error(
    mock_resolve,
    mock_conn,
) -> None:
    mock_resolve.return_value.entity_id = "service:mcp-server"
    mock_conn.return_value.__enter__ = lambda *_: None  # type: ignore[method-assign]
    mock_conn.return_value.__exit__ = lambda *_: None  # type: ignore[method-assign]

    result = execute_op(
        "analyze_impact",
        {
            "entity_id": "service:mcp-server",
            "claim": "public dispatch path invalid confidence",
            "confidence": "not_a_valid_band",
        },
    )
    assert "error" in result
    assert "Invalid confidence" in result["error"]


@pytest.mark.offline
@patch("cortex_store.dispatch_ops.ops_assertions.analyze_impact_semantic")
def test_execute_op_analyze_impact_isolated_db_omitted_confidence_defaults_believed(
    mock_semantic,
    analyze_impact_db: Path,
) -> None:
    mock_semantic.return_value.model_dump.return_value = {"impact_score": 0.0}

    result = execute_op(
        "analyze_impact",
        {
            "entity_id": "service:mcp-server",
            "claim": "isolated DB public path default confidence",
        },
    )
    assert "error" not in result, result
    req = mock_semantic.call_args[0][0]
    assert isinstance(req, ImpactAnalysisRequest)
    assert req.confidence == "believed"


@pytest.mark.offline
@patch("cortex_store.dispatch_ops.ops_assertions.analyze_impact_semantic")
def test_execute_op_analyze_impact_isolated_db_explicit_confidence_forwarded(
    mock_semantic,
    analyze_impact_db: Path,
) -> None:
    mock_semantic.return_value.model_dump.return_value = {"impact_score": 0.0}

    result = execute_op(
        "analyze_impact",
        {
            "entity_id": "service:mcp-server",
            "claim": "isolated DB public path explicit confidence",
            "confidence": "confirmed",
        },
    )
    assert "error" not in result, result
    req = mock_semantic.call_args[0][0]
    assert req.confidence == "confirmed"


@pytest.mark.offline
def test_execute_op_analyze_impact_isolated_db_invalid_confidence_returns_error(
    analyze_impact_db: Path,
) -> None:
    result = execute_op(
        "analyze_impact",
        {
            "entity_id": "service:mcp-server",
            "claim": "isolated DB public path invalid confidence",
            "confidence": "not_a_valid_band",
        },
    )
    assert "error" in result
    assert "Invalid confidence" in result["error"]
