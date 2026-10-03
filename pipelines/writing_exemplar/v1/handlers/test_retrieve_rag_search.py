"""writing_exemplar retrieve must call rag-search with prefix pipeline_options."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from systems.pipeline.core.dag import PipelineExecutionError
from systems.pipeline.core.handlers.protocol import StepOutput

from pipelines.writing_exemplar.v1.handlers.register_bind import prefixes_for_register
from pipelines.writing_exemplar.v1.handlers.retrieve import (
    WritingExemplarRetrieveHandler,
)

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_retrieve_posts_rag_search_with_prefixes_and_response_shape() -> None:
    """Break: wrong pipeline id or dropped rag_source_prefixes breaks register bind."""
    captured: dict[str, object] = {}

    async def _fake_pipeline_call(step: object, _context: object) -> StepOutput:
        get = getattr(step, "get_domain_field")
        captured["pipeline_id"] = get("pipeline_id")
        captured["pipeline_options"] = dict(get("pipeline_options") or {})
        return StepOutput(
            raw="[Source: sample.md]\n\nExemplar body.",
            json={
                "retrieval": {
                    "chunks_found": 1,
                    "resolved_scope": "writing_exemplars",
                }
            },
        )

    step = MagicMock()
    step.id = "retrieve_exemplars"
    step.timeout_seconds = 90
    step.handler_timeout_seconds = None
    step.get_domain_field.side_effect = lambda key, default=None: (
        {} if key == "pipeline_options" else default
    )

    context = MagicMock()
    context.options = {"register": "familiar"}
    context.source_text = "tone for a personal essay"

    handler = WritingExemplarRetrieveHandler()
    with patch(
        "pipelines.writing_exemplar.v1.handlers.retrieve._PIPELINE_CALL.execute",
        new=AsyncMock(side_effect=_fake_pipeline_call),
    ):
        out = await handler.execute(step, context)

    assert captured["pipeline_id"] == "rag-search"
    options = captured["pipeline_options"]
    assert isinstance(options, dict)
    prefixes = options.get("rag_source_prefixes")
    assert isinstance(prefixes, list)
    assert prefixes and prefixes[0].endswith("/familiar")

    assert out.json is not None
    assert out.json.get("register") == "familiar"
    assert out.json.get("rag_source_prefixes") == prefixes
    assert out.json.get("retrieval", {}).get("chunks_found") == 1
    assert "[Source: sample.md]" in out.raw


def test_removed_register_fails_closed_with_known_names() -> None:
    """Break: letters/oratory/travel have no prefix directory and returned empty context."""
    with pytest.raises(PipelineExecutionError, match="Unknown writing_exemplar register 'letters'") as exc:
        prefixes_for_register("letters")
    message = str(exc.value)
    assert "familiar" in message
    assert "humor" in message
    assert "civic" in message
    for gone in ("oratory", "travel", "letter", "speech"):
        with pytest.raises(PipelineExecutionError, match="Unknown writing_exemplar register"):
            prefixes_for_register(gone)
