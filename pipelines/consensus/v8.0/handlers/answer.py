"""Generate a candidate answer from a single model in the answer pool.

Each model in the consensus pipeline independently answers the user's
question through this handler.  The raw answer text feeds into the
verify sub-pipeline, where it is decomposed into atomic claims and
cross-verified by other models.

For multi-domain queries (where ``domain_questions`` is present), the
handler selects a structured prompt variant that addresses each domain
explicitly, building ``domain_sections`` from the domain-to-question
map.  Single-domain queries use the standard answer prompt.

Provenance metadata (model_id, step_id) is attached to the output so
that downstream decompose steps can trace each claim back to its
originating model — this enables the exclude_self logic in
verify_general (the originator is excluded from its own verification
pool).

Invariant: ∀ domain ∈ domain_questions: ∃ section in prompt addressing domain
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.handlers.generate import GenericGenerateHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import PromptConfig, StepConfig

logger = get_logger(__name__)


class ConsensusAnswerHandler(GenericGenerateHandler):
    """Produce a candidate answer for one model in the consensus pool.

    Extends GenericGenerateHandler with domain-aware prompt selection:
    single-domain queries use the standard answer prompt, multi-domain
    queries switch to ``answer_domain_structured`` with explicit
    per-domain sections.  Attaches provenance for downstream tracing.
    """

    step_type: str = "consensus_answer_v8_0"

    def __init__(self) -> None:
        super().__init__()

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Execute answer step with domain-aware prompt selection."""
        start_time = time.time()

        domain_questions = self._resolve_domain_questions(step, context)
        prompt_ref = self._select_prompt_ref(step, domain_questions)

        registry = context._registry
        prompt_config = registry.get_prompt(prompt_ref)
        model_config = registry.get_model_config(
            step.model_ref,
            domain=context.pipeline.domain,
            search_path=context.pipeline.source_search_path,
        )

        resolved = self._resolve_execution_config(
            step, prompt_config, model_config, context
        )
        logger.debug(f"[TEMP] Resolved execution config: {resolved!r}")

        prompt_context = self._build_answer_prompt_context(
            step, context, domain_questions
        )

        rendered = self._render_prompt(prompt_ref, prompt_context, context)

        logger.debug(
            f"Answer step '{step.id}': "
            f"model={resolved['model_id']}, "
            f"prompt_ref={prompt_ref}, "
            f"domains={list(domain_questions.keys()) if domain_questions else None}"
        )

        call_result = await self._call_model(
            resolved["model_id"],
            rendered.user_prompt,
            step,
            context,
            resolved["system_prompt"],
            temperature=resolved["temperature"],
            max_tokens=resolved["max_tokens"],
            json_schema=resolved["json_schema"],
            model_id_is_resolved=True,
        )

        latency_ms = (time.time() - start_time) * 1000
        output = self._build_step_output(call_result, resolved, latency_ms, step.id)
        output = self._add_provenance_to_output(output, resolved["model_id"], step.id)

        return output

    def _resolve_domain_questions(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> dict[str, str] | None:
        """Resolve domain_questions from handler_inputs."""
        from systems.pipeline.core.execution.resolver import (
            NamespaceResolver,
            traverse_path,
        )

        if not step.handler_inputs or "domain_questions" not in step.handler_inputs:
            return None

        try:
            resolver = NamespaceResolver(context)
            binding = step.handler_inputs["domain_questions"]
            root = resolver.resolve(binding)
            value = traverse_path(
                root,
                binding.field_path,
                step_name=step.id,
                field_name="domain_questions",
                binding_repr=str(binding),
                resolver=resolver,
            )

            if value is None:
                return None
            if isinstance(value, dict) and value:
                return value

            return None

        except Exception as e:
            logger.warning(f"Failed to resolve domain_questions: {e}")
            return None

    def _select_prompt_ref(
        self,
        step: StepConfig,
        domain_questions: dict[str, str] | None,
    ) -> str:
        """Select prompt reference based on domain_questions."""
        base_ref = step.prompt_ref
        if not base_ref:
            raise ValueError(f"Step '{step.id}' missing prompt_ref")

        if domain_questions:
            if base_ref.endswith(".answer"):
                return base_ref.replace(".answer", ".answer_domain_structured")
            return f"{base_ref}_domain_structured"

        return base_ref

    def _build_answer_prompt_context(
        self,
        step: StepConfig,
        context: PipelineContext,
        domain_questions: dict[str, str] | None,
    ) -> dict[str, Any]:
        """Build prompt context with domain_sections for multi-domain queries."""
        prompt_context = self._build_prompt_context(step, context)

        if domain_questions:
            domain_sections = self._format_domain_sections(domain_questions)
            prompt_context["domain_sections"] = domain_sections
            logger.info(
                f"Step '{step.id}': Built domain_sections for "
                f"{len(domain_questions)} domains"
            )

        return prompt_context

    def _format_domain_sections(self, domain_questions: dict[str, str]) -> str:
        """
        Format domain questions as structured sections.

        Output format:
        **Mathematics**: What are the mathematical properties of 42?
        **Physics**: What scientific occurrences involve 42?
        """
        lines = []
        for domain, question in domain_questions.items():
            display_name = domain.replace("_", " ").title()
            lines.append(f"**{display_name}**: {question}")

        return "\n".join(lines)

    def _add_provenance_to_output(
        self,
        output: StepOutput,
        model_id: str,
        step_id: str,
    ) -> StepOutput:
        """
        Add provenance data to step output.

        Provenance tracks content origination for decomposition chain.
        Required by decompose_all step which extracts source provenance.
        """
        from provenance import create_provenance

        prov = create_provenance(model_id=model_id, step_id=step_id)

        if output.json is None:
            output_json = {"provenance": prov.to_dict()}
        else:
            output_json = dict(output.json)
            output_json["provenance"] = prov.to_dict()

        return StepOutput(
            raw=output.raw,
            json=output_json,
            model_id=output.model_id,
            step_id=output.step_id,
            latency_ms=output.latency_ms,
            prompt_tokens=output.prompt_tokens,
            completion_tokens=output.completion_tokens,
            system_prompt=output.system_prompt,
            user_prompt=output.user_prompt,
            temperature=output.temperature,
            max_tokens=output.max_tokens,
            request_body=output.request_body,
            provenance=output.provenance,
            error=output.error,
        )

    def _resolve_execution_config(
        self,
        step: StepConfig,
        prompt_config: PromptConfig,
        model_config: Any,
        context: PipelineContext,
    ) -> dict[str, Any]:
        """
        Resolve execution configuration.

        Generation parameters hierarchy:
        1. Step generation_parameters (explicit)
        2. Pipeline token_defaults (fallback)
        3. Dynamic adjustment based on expansion_safe

        System prompt uses hierarchy: prompt > model > "".
        """
        system_prompt = prompt_config.system_prompt or model_config.system_prompt or ""

        temperature = step.generation_parameters.get("temperature")
        max_tokens = self._resolve_max_tokens(step, context)

        json_schema = None
        if step.generation_parameters.get("response_format"):
            rf = step.generation_parameters["response_format"]
            if rf.get("type") == "json_object" and rf.get("schema"):
                json_schema = rf["schema"]
        elif prompt_config.json_schema:
            json_schema = prompt_config.json_schema

        return {
            "model_id": model_config.model,
            "system_prompt": system_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "json_schema": json_schema,
        }

    @override
    def validate(self, step: StepConfig) -> list[str]:
        """Validate step configuration."""
        errors = []
        if not step.model_ref:
            errors.append(f"Step '{step.id}' missing model_ref")
        if not step.prompt_ref:
            errors.append(f"Step '{step.id}' missing prompt_ref")
        if not step.handler_inputs or "question" not in step.handler_inputs:
            errors.append(f"Step '{step.id}' missing 'question' in handler_inputs")
        return errors
