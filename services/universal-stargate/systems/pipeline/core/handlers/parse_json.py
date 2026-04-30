"""Built-in `parse_json_v1` step: deterministic JSON-string parse.

Bridges handlers that emit JSON-as-string in their output (notably
`frontier_dispatch_v1.json.content` when `response_format: json_object`
is set, and `pipeline_call_v1.raw` for sub-pipeline calls) to downstream
steps that need real dicts/lists at binding time — e.g. `map_over` cannot
iterate a JSON string, it needs a real list.

Decision context: thread 759 (P3, option γ). Design rationale lives in
`notes/system/consultations/consult-and-overhaul-pipeline-design-response-2026-04-30.md`
§3.2 + §4.2 + §4.3.

YAML usage:

    - name: brief_compose_parsed
      type: parse_json_v1
      handler_inputs:
        source: brief_compose.json.content   # binding to a JSON string

The handler resolves `source` to a string, calls `json.loads`, and exposes
the result as `StepOutput.json`. Top-level result MUST be a JSON object —
arrays/scalars require an explicit wrapping object upstream so the
`step.json.<field>` binding pattern stays uniform.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from universal_logging import get_logger

from ..execution.resolver import NamespaceResolver, traverse_path
from .protocol import StepOutput
from .registry import register_handler

if TYPE_CHECKING:
    from ..schemas import StepConfig
    from .protocol import PipelineContext

logger = get_logger(__name__)


@register_handler
class ParseJsonV1Handler:
    """Parse a JSON-string upstream binding into structured `StepOutput.json`.

    Stateless. Pure-Python. Adds zero LLM cost.
    """

    step_type = "parse_json_v1"

    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        binding = step.handler_inputs.get("source")
        if binding is None:
            raise ValueError(
                f"Step '{step.id}': parse_json_v1 requires handler_inputs.source"
            )

        resolver = NamespaceResolver(context)
        root = resolver.resolve(binding)
        raw = traverse_path(
            root,
            binding.field_path,
            step_name=step.id,
            field_name="source",
            binding_repr=str(binding),
            resolver=resolver,
        )

        if not isinstance(raw, str):
            raise ValueError(
                f"Step '{step.id}': parse_json_v1 expected string at "
                f"handler_inputs.source ({binding}), got {type(raw).__name__}"
            )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            snippet = raw[:200].replace("\n", "\\n")
            raise ValueError(
                f"Step '{step.id}': parse_json_v1 failed to parse JSON from "
                f"handler_inputs.source ({binding}): {exc.msg} at "
                f"line {exc.lineno} col {exc.colno}. Snippet: {snippet!r}"
            ) from exc

        if not isinstance(parsed, dict):
            raise ValueError(
                f"Step '{step.id}': parse_json_v1 expected a JSON object at "
                f"top-level, got {type(parsed).__name__}. Wrap arrays/scalars "
                'in an object upstream: {"items": [...]}'
            )

        return StepOutput(raw=raw, json=parsed)

    def validate(self, step: StepConfig) -> list[str]:
        if "source" not in step.handler_inputs:
            return [
                f"Step '{step.id}': parse_json_v1 requires "
                "handler_inputs.source binding"
            ]
        return []
