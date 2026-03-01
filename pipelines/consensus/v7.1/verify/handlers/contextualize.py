"""Add topic-anchoring context to claims before verification.

Takes atomic claims from atomicity_gate and uses an LLM to produce a
bare topic string for each claim (e.g. "TCP", "quantum mechanics").
The topic is derived from the question alone — claims are already fully
self-contained after decompose + atomicity_gate, so the full answer is
not needed.  The topic is stored in ``context_prefix``; ``original_text``
is a copy of ``text``.  The ``text`` field is NOT modified.

At verification time, formatters use ``context_prefix`` and
``original_text`` to present claims with XML structural separation,
preventing the topic from priming verifiers toward acceptance.

Claims are chunked and distributed across the contextualize pool via
round-robin, enabling parallel processing.

Invariant:
    ∀ claim ∈ output: claim.text == claim.original_text (unmodified)

Outputs:
    json.claims — claim list with ``context_prefix`` and ``original_text``
                   fields added; ``text`` unchanged
"""

from __future__ import annotations

import asyncio
import json
from itertools import batched
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.chunked.model_config import get_execution_config
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ...shared._chain_utils import strip_json_fences, token_budget

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_CONTEXTUALIZE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "context": {"type": "string"},
                },
                "required": ["index", "context"],
            },
        },
    },
    "required": ["claims"],
}


class ContextualizeHandler(BaseHandler):
    """Add topic-anchoring context prefix before cross-model verification.

    Each claim receives a short prefix that anchors it to the question's
    topic.  The original claim text is preserved verbatim.
    """

    step_type: str = "consensus_contextualize_v7_1"

    def _resolve_pool(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> list[str]:
        """Resolve contextualize model pool from step config."""
        pool_raw = step.get_domain_field("model_pool")
        if isinstance(pool_raw, list):
            return list(pool_raw)
        if isinstance(pool_raw, str):
            opts = context.options or {}
            resolved = opts.get(pool_raw.removeprefix("optionsNs."), [])
            if isinstance(resolved, list):
                return list(resolved)
        return []

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        resolver = NamespaceResolver(context)
        claims: list[dict[str, Any]] = self._resolve_input(
            resolver, step, "claims", step.handler_inputs
        )
        question: str = str(
            self._resolve_input(resolver, step, "question", step.handler_inputs) or ""
        )

        if not claims:
            return StepOutput(raw="", json={"claims": []})

        pool_aliases = self._resolve_pool(step, context)
        if not pool_aliases:
            logger.warning(
                "Step '%s': no contextualize pool — passing claims through", step.id
            )
            return StepOutput(raw="", json={"claims": claims})

        pool_ids = [self._resolve_model_alias(a, context) for a in pool_aliases]

        prompt_ref = self._require_domain_field(step, "prompt_ref_contextualize")

        step_chunk = (
            context.pipeline.options.get("contextualize_chunk_size")
            or step.get_domain_field("chunk_size")
            or context.pipeline.options.get("default_chunk_size")
        )
        if step_chunk is None:
            raise ValueError(
                "Chunk size required: set pipeline options default_chunk_size "
                "or contextualize_chunk_size, or step domain field chunk_size"
            )
        step_chunk = int(step_chunk)

        registry = context._registry
        model_chunks = [
            get_execution_config(
                registry.get_model_config(
                    a,
                    domain=context.pipeline.domain,
                    search_path=context.pipeline.source_search_path,
                )
            ).chunk_size
            for a in pool_aliases
        ]
        chunk_size = min(step_chunk, *model_chunks) if model_chunks else step_chunk

        chunks = list(batched(range(len(claims)), chunk_size))

        results: list[list[dict[str, Any]] | None] = [None] * len(chunks)

        async def _process_chunk(
            chunk_idx: int,
            indices: tuple[int, ...],
            model_id: str,
        ) -> None:
            chunk_claims = [claims[i] for i in indices]
            numbered = "\n".join(
                f"[{i}] {c.get('text', '')}" for i, c in enumerate(chunk_claims)
            )

            rendered = self._render_prompt(
                prompt_ref,
                {
                    "cleaned_question": question,
                    "claim_count": len(chunk_claims),
                    "numbered_claims": numbered,
                },
                context,
                safe=True,
            )

            max_tok = self._constrained_tokens(
                token_budget(context, "verify_contextualize", 2048), context
            )

            result = await self._call_model(
                model_id,
                rendered.user_prompt,
                step,
                context,
                system_prompt=rendered.system_prompt,
                temperature=0.0,
                max_tokens=max_tok,
                json_schema=_CONTEXTUALIZE_SCHEMA,
                call_label="contextualize",
            )

            prefixed = _apply_context_prefixes(result.content, step.id, chunk_claims)
            results[chunk_idx] = prefixed

        async with asyncio.TaskGroup() as tg:
            for ci, idx_batch in enumerate(chunks):
                model_id = pool_ids[ci % len(pool_ids)]
                tg.create_task(_process_chunk(ci, idx_batch, model_id))

        merged: list[dict[str, Any]] = []
        for ci, idx_batch in enumerate(chunks):
            chunk_result = results[ci]
            if chunk_result is None:
                merged.extend(claims[i] for i in idx_batch)
            else:
                merged.extend(chunk_result)

        return StepOutput(raw="", json={"claims": merged})


def _apply_context_prefixes(
    content: str,
    step_id: str,
    original_claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Parse LLM topic strings and store as structured claim fields.

    For each claim with a non-empty topic from the LLM:
      - ``context_prefix``: bare topic (e.g. "TCP")
      - ``original_text``: copy of ``text`` (redundant but explicit)

    The ``text`` field is NOT modified — it stays as the original claim
    for clean display in the viewer and clean input to synthesis.
    The verification formatter reads ``context_prefix`` and ``original_text``
    to build XML-structured input for verifiers.

    Falls back to originals on parse failure.
    """
    try:
        cleaned = strip_json_fences(content)
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "Step '%s': failed to parse contextualize response, "
            "keeping original claims: %s",
            step_id,
            exc,
        )
        return list(original_claims)

    prefix_list = data.get("claims", [])
    topic_map: dict[int, str] = {}
    for entry in prefix_list:
        idx = entry.get("index")
        ctx = entry.get("context")
        if idx is not None and ctx is not None:
            topic_map[idx] = ctx.strip()

    result: list[dict[str, Any]] = []
    applied = 0
    for i, claim in enumerate(original_claims):
        updated = dict(claim)
        topic = topic_map.get(i, "")
        if topic:
            updated["original_text"] = updated.get("text", "")
            updated["context_prefix"] = topic
            applied += 1
        result.append(updated)

    logger.info(
        "Step '%s': anchored %d/%d claims with context prefix",
        step_id,
        applied,
        len(original_claims),
    )

    return result
