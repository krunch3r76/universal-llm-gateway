"""MCP validate surfaces Stargate catalog_skips instead of a bare not-found."""

from __future__ import annotations

from unittest.mock import patch

from tools.pipeline import _pipeline_validate


def test_validate_uses_catalog_skip_when_pipeline_absent() -> None:
    payload = {
        "pipelines": {"ok-pipe": {"steps": 1, "models": ["ok"], "domain": "ok_domain"}},
        "catalog_skips": [
            {
                "pipeline_id": "ghost-pipe",
                "alias": "expand",
                "reason": "missing_domain_models_yaml",
                "expected_models_yaml": "/tmp/ghost_domain/models.yaml",
            }
        ],
    }
    with patch("tools.pipeline._fetch_pipelines_metadata", return_value=payload):
        result = _pipeline_validate("ghost-pipe")
    assert result["valid"] is False
    message = result["errors"][0]
    assert "ghost-pipe" in message
    assert "expand" in message
    assert "missing_domain_models_yaml" in message
    assert "/tmp/ghost_domain/models.yaml" in message


def test_validate_unknown_id_without_skip_stays_not_found() -> None:
    payload = {"pipelines": {}, "catalog_skips": []}
    with patch("tools.pipeline._fetch_pipelines_metadata", return_value=payload):
        result = _pipeline_validate("no-such")
    assert result["valid"] is False
    assert "not found" in result["errors"][0]
