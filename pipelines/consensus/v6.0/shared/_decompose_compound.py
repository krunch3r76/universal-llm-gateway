"""Decompose compound claims into atomic sub-claims.

v6.0 prefers an LLM atomicity classifier (batch) to identify which claims are
COMPOUND, then decomposes only those via per-claim LLM calls. This avoids
brittle string heuristics that tend to over-trigger on conditional language.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from universal_logging import get_logger

from ._chain_utils import token_budget

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.builtin import BaseHandler
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_ATOMICITY_CLASSIFY_MAX_CLAIMS = 32
_ATOMICITY_CLASSIFY_MAX_CHARS = 9000

_DECOMPOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sub_claims": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["sub_claims"],
}


def _batch_candidates_for_atomicity_classify(
    candidates: list[tuple[int, dict[str, Any]]],
    *,
    max_claims: int,
    max_chars: int,
) -> list[list[tuple[int, dict[str, Any]]]]:
    """Batch candidates for the atomicity classifier prompt.

    Batches are constrained by claim count and approximate character count to
    avoid oversized prompts.
    """
    if max_claims < 1:
        raise ValueError("max_claims must be >= 1")
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")

    batches: list[list[tuple[int, dict[str, Any]]]] = []
    current: list[tuple[int, dict[str, Any]]] = []
    current_chars = 0

    for real_idx, claim in candidates:
        text = str(claim.get("text") or "").strip()
        est_chars = len(text) + 8  # numbering + newline overhead
        if current and (
            len(current) >= max_claims or current_chars + est_chars > max_chars
        ):
            batches.append(current)
            current = []
            current_chars = 0
        current.append((real_idx, claim))
        current_chars += est_chars

    if current:
        batches.append(current)

    return batches


async def _decompose_one_claim(
    handler: BaseHandler,
    claim: dict[str, Any],
    model_id: str,
    step: StepConfig,
    context: PipelineContext,
    prompt_ref: str,
    max_tokens: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]] | None:
    """Decompose a single claim into sub-claims via LLM.

    Returns (parent_with_children, sub_claim_dicts, event_detail) or None
    if the LLM returned ≤1 sub-claim (i.e. claim is actually atomic).
    """
    parent_id = claim.get("statement_id", "")
    text = (claim.get("text") or "").strip()

    try:
        rendered = handler._render_prompt(
            prompt_ref,
            {"statement": text},
            context,
            safe=True,
        )
        call_result = await handler._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            system_prompt=rendered.system_prompt,
            temperature=0.0,
            max_tokens=max_tokens,
            json_schema=_DECOMPOSE_SCHEMA,
        )
        data = json.loads(call_result.content.strip())
        sub_texts_raw = data.get("sub_claims") or []
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(
            "Step '%s': decompose failed for claim %s: %s",
            step.id,
            parent_id[:8] if parent_id else "?",
            e,
        )
        return None

    if not isinstance(sub_texts_raw, list):
        return None

    valid = [t.strip() for t in sub_texts_raw if isinstance(t, str) and t.strip()]
    if len(valid) <= 1:
        logger.info(
            "Step '%s': LLM returned %d sub-claim(s) for '%s' — keeping original",
            step.id,
            len(valid),
            parent_id[:8] if parent_id else "?",
        )
        return None

    # Propagate provenance only when parent has a well-formed one;
    # claims from decompose_answer may lack provenance entirely
    parent_prov = claim.get("provenance")
    has_prov = isinstance(parent_prov, dict) and "originator_model_id" in parent_prov

    parent_out = dict(claim)
    parent_out["has_sub_claims"] = True

    subs: list[dict[str, Any]] = []
    subs_event: list[dict[str, Any]] = []

    for sub_text in valid:
        sub_id = str(uuid4())
        sub: dict[str, Any] = {
            "statement_id": sub_id,
            "text": sub_text,
            "claim_type": "direct",
            "domain": claim.get("domain", "general"),
            "parent_statement_id": parent_id,
            "parent_text": text,
        }
        if has_prov:
            assert isinstance(parent_prov, dict)
            lineage = list(parent_prov.get("lineage") or [])
            sub["provenance"] = {
                **parent_prov,
                "lineage": [*lineage, f"decompose_compound:{model_id}"],
            }
        subs.append(sub)
        subs_event.append({"statement_id": sub_id, "text": sub_text})

    detail = {"parent_id": parent_id, "parent_text": text, "sub_claims": subs_event}
    return (parent_out, subs, detail)


async def atomicity_gate_decompose(
    handler: BaseHandler,
    claims: list[dict[str, Any]],
    model_id: str,
    step: StepConfig,
    context: PipelineContext,
    prompt_ref_classify: str,
    prompt_ref_decompose: str,
    *,
    domains: frozenset[str] = frozenset({"general"}),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Decompose compound claims via atomicity classification.

    Phase 1: batch-classify candidate claims as ATOMIC vs COMPOUND (LLM).
    Phase 2: decompose LLM-identified compounds via per-claim LLM calls.

    Returns (updated_claims, decomposed_details).
    """
    # Phase 0: select candidates — in target domains, not already split
    candidates: list[tuple[int, dict[str, Any]]] = []
    for i, claim in enumerate(claims):
        if claim.get("domain", "general") not in domains:
            continue
        if claim.get("has_sub_claims") or claim.get("parent_statement_id"):
            continue
        candidates.append((i, claim))

    if not candidates:
        return (claims, [])

    classify_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "compound_indices": {
                "type": "array",
                "items": {"type": "integer"},
            },
        },
        "required": ["compound_indices"],
    }
    classify_tokens = handler._constrained_tokens(
        token_budget(context, "atomicity_classify", 512),
        context,
    )

    # Phase 1: batch-classify atomicity
    compound_real: set[int] = set()
    batches = _batch_candidates_for_atomicity_classify(
        candidates,
        max_claims=_ATOMICITY_CLASSIFY_MAX_CLAIMS,
        max_chars=_ATOMICITY_CLASSIFY_MAX_CHARS,
    )
    for batch_i, batch in enumerate(batches):
        numbered = "\n".join(
            f"[{ci}] {str(c.get('text') or '').strip()}"
            for ci, (_, c) in enumerate(batch)
        )
        try:
            rendered = handler._render_prompt(
                prompt_ref_classify,
                {"numbered_claims": numbered},
                context,
                safe=True,
            )
            call_result = await handler._call_model(
                model_id,
                rendered.user_prompt,
                step,
                context,
                system_prompt=rendered.system_prompt,
                temperature=0.0,
                max_tokens=classify_tokens,
                json_schema=classify_schema,
            )
            data = json.loads(call_result.content.strip())
            compound_indices = set(data.get("compound_indices") or [])
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(
                "Step '%s': atomicity classify failed (batch %d/%d): %s",
                step.id,
                batch_i + 1,
                len(batches),
                e,
            )
            continue

        for batch_idx in compound_indices:
            if not isinstance(batch_idx, int):
                continue
            if batch_idx < 0 or batch_idx >= len(batch):
                continue
            real_idx = batch[batch_idx][0]
            compound_real.add(real_idx)

    if not compound_real:
        logger.info(
            "Step '%s': atomicity gate — all %d candidates are atomic",
            step.id,
            len(candidates),
        )
        return (claims, [])

    logger.info(
        "Step '%s': atomicity gate — %d/%d candidates identified as compound",
        step.id,
        len(compound_real),
        len(candidates),
    )

    # Phase 2: decompose identified compounds
    decompose_tokens = handler._constrained_tokens(
        token_budget(context, "atomicity_decompose", 1024),
        context,
    )
    updated: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    for i, claim in enumerate(claims):
        if i not in compound_real:
            updated.append(claim)
            continue

        result = await _decompose_one_claim(
            handler,
            claim,
            model_id,
            step,
            context,
            prompt_ref_decompose,
            decompose_tokens,
        )
        if result is None:
            updated.append(claim)
            continue

        parent_out, subs, detail = result
        updated.append(parent_out)
        updated.extend(subs)
        details.append(detail)

    return (updated, details)
