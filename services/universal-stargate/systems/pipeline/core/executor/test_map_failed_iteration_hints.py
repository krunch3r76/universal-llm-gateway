"""Map failed-iteration hints and json_array stability for index pipelines."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

_stargate_root = str(Path(__file__).resolve().parents[4])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from systems.pipeline.core.execution.map_reduce.map_output_collection import (  # noqa: E402
    MapOutputCollection,
)
from systems.pipeline.core.executor.output_resolution import (  # noqa: E402
    extract_output_hints,
    get_final_result,
)
from systems.pipeline.core.handlers import PipelineContext  # noqa: E402
from systems.pipeline.core.handlers.step_output import StepOutput  # noqa: E402
from systems.pipeline.core.schemas import PipelineSpec  # noqa: E402


def _pipeline(output_step: str, *, pipeline_id: str, category: str) -> PipelineSpec:
    return PipelineSpec.model_validate(
        {
            "schema_version": 6,
            "id": pipeline_id,
            "version": "1",
            "type": category,
            "category": category,
            "output": output_step,
            "output_format": "json_array",
            "steps": [],
        }
    )


def _context(pipeline: PipelineSpec) -> PipelineContext:
    return PipelineContext(
        pipeline=pipeline,
        source_text="",
        http_request=MagicMock(),
        execution_id="exec-test",
    )


def _json_array_for_collection(
    collection: MapOutputCollection,
    *,
    output_step: str = "contextualize",
    pipeline_id: str = "rag-contextualize",
    category: str = "rag_contextualize",
) -> str:
    pipeline = _pipeline(output_step, pipeline_id=pipeline_id, category=category)
    context = _context(pipeline)
    context.outputs[output_step] = collection
    return get_final_result(pipeline, context)


def test_threshold_met_map_surfaces_failed_iteration_hints() -> None:
    outputs = [
        StepOutput(raw='{"ok": 1}', json={"ok": 1}),
        StepOutput(raw='{"ok": 2}', json={"ok": 2}),
    ]
    collection = MapOutputCollection(
        outputs,
        output_positions=[0, 2],
        total_count=3,
        failed_iteration_hints=[
            {
                "index": 1,
                "error_message": "Prompt too large for model m: ~9000 tokens",
            }
        ],
    )
    pipeline = _pipeline(
        "extract_batch", pipeline_id="rag-extraction", category="rag_extraction"
    )
    context = _context(pipeline)
    context.outputs["extract_batch"] = collection

    hints = extract_output_hints(pipeline, context)
    assert hints == [
        {
            "index": 1,
            "error_message": "Prompt too large for model m: ~9000 tokens",
        }
    ]

    payload = json.loads(_json_array_for_collection(collection, output_step="extract_batch"))
    assert payload == [{"ok": 1}, None, {"ok": 2}]


def test_fully_successful_map_json_unchanged_with_empty_hints() -> None:
    """rag-contextualize-v1 / vocab-classify map steps: no hint → same json_array."""
    outputs = [
        StepOutput(raw='{"prefix":"a"}', json={"prefix": "a"}),
        StepOutput(raw='{"prefix":"b"}', json={"prefix": "b"}),
    ]
    baseline = MapOutputCollection(
        outputs,
        output_positions=[0, 1],
        total_count=2,
    )
    with_hints_field = MapOutputCollection(
        outputs,
        output_positions=[0, 1],
        total_count=2,
        failed_iteration_hints=None,
    )

    baseline_json = _json_array_for_collection(baseline, output_step="contextualize")
    with_field_json = _json_array_for_collection(
        with_hints_field, output_step="contextualize"
    )
    assert baseline_json == with_field_json == json.dumps(
        [{"prefix": "a"}, {"prefix": "b"}]
    )

    pipeline = _pipeline(
        "reconcile", pipeline_id="vocab-classify-v1", category="vocab_classify"
    )
    context = _context(pipeline)
    context.outputs["reconcile"] = baseline
    assert extract_output_hints(pipeline, context) is None
