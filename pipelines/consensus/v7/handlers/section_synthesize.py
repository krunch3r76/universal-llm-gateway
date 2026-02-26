"""Per-section synthesis handler — one focused model call per outline section."""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any, cast, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from .shared._chain_utils import strip_json_fences

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig


def _extract_fact_texts(value: Any) -> list[str]:
    """Extract fact text strings from verified-facts input shapes."""
    if isinstance(value, list) and value:
        if isinstance(value[0], dict):
            return [
                str(item.get("text", "")).strip() for item in value if item.get("text")
            ]
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [line.strip() for line in value.splitlines() if line.strip()]
    return []


def _parse_outline(raw: str) -> dict[str, Any] | None:
    """Parse outline JSON, stripping markdown fences. Returns None on failure."""
    try:
        parsed = json.loads(strip_json_fences(raw))
        if isinstance(parsed, dict) and isinstance(parsed.get("sections"), list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _slugify(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", heading.lower()).strip("_")


class SectionSynthesizeHandler(BaseHandler):
    """Synthesize one prose section per outline section with only its own facts visible."""

    step_type: str = "consensus_section_synthesize_v7"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        start_time = time.time()
        resolver = NamespaceResolver(cast(Any, context))
        hi = step.handler_inputs or {}
        resolved: dict[str, Any] = {
            name: self._resolve_input(resolver, step, name, hi) for name in hi
        }

        outline_raw = str(resolved.get("outline", ""))
        verified_facts_raw = resolved.get("verified_facts", [])
        question = str(resolved.get("question", ""))

        prompt_ref = step.get_domain_field("prompt_ref") or step.prompt_ref
        if not prompt_ref:
            raise ValueError(f"Step '{step.id}' missing prompt_ref")
        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")

        model_id = self._resolve_model_alias(step.model_ref, context)
        gen_params = step.generation_parameters or {}
        temperature = gen_params.get("temperature", 0.1)
        max_tokens = gen_params.get("max_tokens")

        outline = _parse_outline(outline_raw)
        if not outline:
            raise ValueError(f"Step '{step.id}': could not parse outline JSON")

        fact_texts = _extract_fact_texts(verified_facts_raw)
        fact_map: dict[int, str] = {i: text for i, text in enumerate(fact_texts, 1)}

        sections = outline["sections"]
        section_outputs: list[str] = []
        total_pt = 0
        total_ct = 0

        for section in sections:
            heading = str(section.get("heading", ""))
            indices: list[int] = [
                i
                for i in section.get("fact_indices", [])
                if isinstance(i, int) and i in fact_map
            ]
            if not indices:
                continue
            section_facts = "".join(f"[{i}] {fact_map[i]}\n" for i in indices)
            call_label = "section_" + _slugify(heading)

            rendered = self._render_prompt(
                prompt_ref,
                {
                    "section_heading": heading,
                    "section_facts": section_facts,
                    "fact_count": str(len(indices)),
                    "question": question,
                },
                context,
                safe=True,
            )

            result = await self._call_model(
                model_id,
                rendered.user_prompt,
                step,
                context,
                system_prompt=rendered.system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                call_label=call_label,
            )
            total_pt += result.prompt_tokens
            total_ct += result.completion_tokens
            section_outputs.append(result.content.strip())

        latency_ms = (time.time() - start_time) * 1000
        return StepOutput(
            raw="\n\n".join(section_outputs),
            step_id=step.id,
            latency_ms=latency_ms,
            prompt_tokens=total_pt,
            completion_tokens=total_ct,
            model_call_count=len(sections),
        )
