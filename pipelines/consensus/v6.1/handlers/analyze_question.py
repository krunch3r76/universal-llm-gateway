"""Classify the user's question and extract structural metadata.

Runs before any answer generation.  An LLM analyzes the raw question
and produces a structured contract that guides downstream synthesis:

- **question_type** (enumeration, comparison, definition, explanation,
  simple, proof) — controls which verification policies and synthesis
  prompts are selected.
- **required_items** — explicit items the answer must cover (e.g. for
  "list the 5 largest planets", each planet is a required item).
- **cardinality** — expected item count (0 when not applicable).
- **ordering** — whether items have a canonical order.
- **structure_notes** — free-text hints for the synthesis prompt.
- **cleaned_question** — normalized question text used as the canonical
  reference throughout the pipeline (prompt rendering, verification).

The contract is consumed by the synthesize step and by post_process
to shape the final answer's structure and coverage.

Outputs:
    json — QuestionContract (see v4_types.py)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver, traverse_path
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cleaned_question": {"type": "string"},
        "question_type": {
            "type": "string",
            "enum": [
                "enumeration",
                "comparison",
                "definition",
                "explanation",
                "simple",
                "proof",
            ],
        },
        "required_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "required": {"type": "boolean"},
                },
                "required": ["label", "required"],
            },
        },
        "cardinality": {
            "type": "integer",
            "description": "Expected number of items (0 if not applicable)",
        },
        "ordering": {
            "type": "string",
            "enum": ["canonical", "alphabetical", "none"],
        },
        "structure_notes": {
            "type": "string",
            "description": "Additional structural requirements",
        },
    },
    "required": [
        "cleaned_question",
        "question_type",
        "required_items",
        "cardinality",
        "ordering",
    ],
}


class AnalyzeQuestionHandler(BaseHandler):
    """Classify the question and emit a structural contract for synthesis.

    The contract (question_type, required_items, cardinality, ordering)
    tells downstream steps what shape the answer should take and which
    verification policies to apply.
    """

    step_type = "consensus_analyze_v4"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Render prompt, call model, validate contract, return output."""
        resolver = NamespaceResolver(context)
        text_binding = step.handler_inputs.get("text")
        if not text_binding:
            raise ValueError(f"Step '{step.id}' missing 'text' in handler_inputs")
        text = traverse_path(
            resolver.resolve(text_binding),
            text_binding.field_path,
            step_name=step.id,
            field_name="text",
            binding_repr=str(text_binding),
            resolver=resolver,
        )
        if not isinstance(text, str):
            raise ValueError(
                f"Step '{step.id}': 'text' must be str, got {type(text).__name__}"
            )

        rendered = self._render_prompt(
            step.prompt_ref,
            {"text": text},
            context,
            safe=True,
        )

        resolved_model = self._resolve_model_alias(step.model_ref, context)
        call_result = await self._call_model(
            resolved_model,
            rendered.user_prompt,
            step,
            context,
            rendered.system_prompt,
            temperature=0.0,
            max_tokens=self._resolve_max_tokens(step, context, handler_default=1024),
            json_schema=CONTRACT_SCHEMA,
        )

        # Check if response was truncated due to length limit
        if call_result.finish_reason == "length":
            logger.warning(
                "analyze_question: model '%s' stopped due to length limit "
                "(tokens: %d prompt + %d completion). Response may be incomplete.",
                resolved_model,
                call_result.prompt_tokens,
                call_result.completion_tokens,
            )

        try:
            contract = json.loads(call_result.content)
        except json.JSONDecodeError as e:
            logger.error("analyze_question: JSON parse failed: %s", e)
            raise

        self._validate_contract(contract)

        return StepOutput(
            raw=call_result.content,
            json=contract,
            prompt_tokens=call_result.prompt_tokens,
            completion_tokens=call_result.completion_tokens,
            model_id=resolved_model,
            step_id=step.id,
        )

    def _validate_contract(self, contract: dict[str, Any]) -> None:
        """Raise if required fields missing or invalid."""
        required = [
            "cleaned_question",
            "question_type",
            "required_items",
            "cardinality",
            "ordering",
        ]
        for key in required:
            if key not in contract:
                logger.error("analyze_question: missing required field %s", key)
                raise ValueError(f"Contract missing required field: {key}")
        if not isinstance(contract.get("required_items"), list):
            logger.error("analyze_question: required_items must be list")
            raise ValueError("required_items must be a list")
        if not isinstance(contract.get("cardinality"), int):
            logger.error("analyze_question: cardinality must be integer")
            raise ValueError("cardinality must be an integer")

    @override
    def validate(self, step: StepConfig) -> list[str]:
        """Validate step configuration."""
        errors: list[str] = []
        if not step.handler_inputs or "text" not in step.handler_inputs:
            errors.append(f"Step '{step.id}' missing 'text' in handler_inputs")
        if not step.prompt_ref:
            errors.append(f"Step '{step.id}' missing prompt_ref")
        if not step.model_ref:
            errors.append(f"Step '{step.id}' missing model_ref")
        return errors
