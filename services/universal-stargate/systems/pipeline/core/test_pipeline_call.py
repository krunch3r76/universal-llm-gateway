"""Tests for pipeline_call_v1 sub-pipeline HTTP error surfacing."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

_repo_root = str(Path(__file__).resolve().parents[5])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from systems.pipeline.core.dag import PipelineExecutionError  # noqa: E402
from systems.pipeline.core.handlers.pipeline_call import (  # noqa: E402
    PipelineCallHandler,
)


@pytest.mark.asyncio
async def test_pipeline_call_surfaces_upstream_error_message() -> None:
    handler = PipelineCallHandler()
    step = MagicMock()
    step.id = "get_context"
    step.handler_timeout_seconds = None
    step.timeout_seconds = 60
    step.get_domain_field.side_effect = lambda key, default=None: {
        "pipeline_id": "rag-context",
        "stargate_url": "http://localhost:9999",
        "pipeline_options": {},
        "consumer_model_ref": "",
    }.get(key, default)

    context = MagicMock()
    context.options = {}
    context.runtime_options = {}
    context.source_text = "question"
    context._registry = None

    response = MagicMock(spec=httpx.Response)
    response.is_error = True
    response.text = "HTTP 500"
    response.json.return_value = {
        "detail": {
            "message": (
                "Step 'relevance_check' response truncated: hit max_tokens limit"
            )
        }
    }

    instance = MagicMock()
    instance.post = AsyncMock(return_value=response)

    with patch(
        "systems.pipeline.core.handlers.pipeline_call.httpx.AsyncClient"
    ) as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        expected_msg = (
            "Sub-pipeline 'rag-context' failed: "
            "Step 'relevance_check' response truncated"
        )
        with pytest.raises(PipelineExecutionError, match=expected_msg):
            await handler.execute(step, context)
