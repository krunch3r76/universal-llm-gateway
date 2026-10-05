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
from systems.pipeline.core.handlers import pipeline_call as pipeline_call_mod  # noqa: E402
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

    client_cm = MagicMock()
    client_cm.__aenter__ = AsyncMock(return_value=instance)
    client_cm.__aexit__ = AsyncMock(return_value=False)
    with patch.object(
        pipeline_call_mod, "make_async_client", return_value=client_cm
    ):
        expected_msg = (
            "Sub-pipeline 'rag-context' failed: "
            "Step 'relevance_check' response truncated"
        )
        with pytest.raises(PipelineExecutionError, match=expected_msg):
            await handler.execute(step, context)
