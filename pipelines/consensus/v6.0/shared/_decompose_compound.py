"""Decompose compound general claims into atomic sub-claims.

Heuristic detection identifies claims with enumeration patterns
(such as, including, e.g., comma-separated lists) and relative/explanatory
clauses (where, which, whose). Detected compounds are decomposed via
a single LLM call per claim.

Non-compound claims pass through unchanged. Mirrors decompose_math pattern.

Optional LLM atomicity gate (Layer 2) catches compounds the heuristic misses
by batch-classifying suspicious long claims, then decomposing identified ones.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from universal_logging import get_logger

from ._chain_utils import token_budget

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.builtin import BaseHandler
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_COMPOUND_MARKERS: list[str] = [
    "such as ",
    "including ",
    "for example, ",
    "for instance, ",
    "e.g., ",
    "e.g. ",
    " as well as ",
]

_LIST_QUANTIFIERS: list[str] = [
    "various ",
    "multiple ",
    "several ",
    "numerous ",
    "countless ",
]

# Relative/explanatory clauses that embed independently verifiable facts
_RELATIVE_CLAUSE_MARKERS: list[str] = [
    ", where ",
    ", which ",
    ", whose ",
    ", in which ",
    ", whereby ",
    ", specifically ",
    ", namely ",
    ", in particular ",
]

# Canonical serial enumeration: "X, Y, and Z" or "X, Y, or Z"
_SERIAL_LIST = re.compile(r"\w+,\s+\w+,?\s+(?:and|or)\s+", re.IGNORECASE)

# Correlative conjunctions: "either X or Y", "both X and Y", etc.
_CORRELATIVE = re.compile(
    r"\b(?:either\s+.+?\s+or\s+"
    r"|both\s+.+?\s+and\s+"
    r"|neither\s+.+?\s+nor\s+"
    r"|not only\s+.+?\s+but(?:\s+also)?\s+)",
    re.IGNORECASE,
)

# Claims shorter than this skip the LLM atomicity gate
_ATOMICITY_MIN_LENGTH = 60

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


def is_compound_general(text: str) -> bool:
    """Detect general claims with enumerated sub-facts or embedded clauses.

    Five detection paths (any triggers decomposition):
    1. Explicit enumeration marker + ≥2 commas
    2. List-quantifier prefix + ≥2 commas
    3. Serial enumeration pattern (A, B, and C)
    4. Relative/explanatory clause embedding a separate verifiable fact
    5. Correlative conjunction (either/or, both/and, neither/nor, not only/but)

    The LLM acts as a second-stage filter: if the claim is actually
    atomic, it returns a single sub-claim and no split occurs.
    """
    lower = text.lower()
    comma_count = text.count(",")

    # Path 1: enumeration marker (e.g. "including", "such as") + commas
    if comma_count >= 2 and any(m in lower for m in _COMPOUND_MARKERS):
        return True

    # Path 2: quantifier prefix (e.g. "various", "countless") + commas
    if comma_count >= 2 and any(q in lower for q in _LIST_QUANTIFIERS):
        return True

    # Path 3: canonical serial list (A, B, and C)
    if _SERIAL_LIST.search(text):
        return True

    # Path 4: relative/explanatory clause with separate verifiable fact
    if any(m in lower for m in _RELATIVE_CLAUSE_MARKERS):
        return True

    # Path 5: correlative conjunction (either X or Y, both X and Y, etc.)
    if _CORRELATIVE.search(text):
        return True

    return False


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


async def decompose_compound_general_claims(
    handler: BaseHandler,
    claims: list[dict[str, Any]],
    model_id: str,
    step: StepConfig,
    context: PipelineContext,
    prompt_ref: str,
    *,
    chunk_size: int = 4096,
    domains: frozenset[str] = frozenset({"general"}),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Decompose heuristic-detected compound claims within specified domains.

    For each claim where domain ∈ domains and is_compound_general(text):
    1. Call LLM with the decompose prompt
    2. Replace parent claim with atomic sub-claims (preserving statement_id linkage)
    3. Record parent→children mapping in decomposed_details for event emission

    Non-compound and out-of-scope-domain claims pass through unchanged.
    Returns (all_claims_with_replacements, decomposition_details).
    """
    updated: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    max_tokens = handler._constrained_tokens(
        token_budget(context, "verify_decompose_compound", 1024),
        context,
    )

    for claim in claims:
        domain = claim.get("domain", "general")
        text = (claim.get("text") or "").strip()
        if domain not in domains or not is_compound_general(text):
            updated.append(claim)
            continue

        result = await _decompose_one_claim(
            handler,
            claim,
            model_id,
            step,
            context,
            prompt_ref,
            max_tokens,
        )
        if result is None:
            updated.append(claim)
            continue

        parent_out, subs, detail = result
        updated.append(parent_out)
        updated.extend(subs)
        details.append(detail)

    return (updated, details)


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
    """LLM atomicity gate: catch compound claims the heuristic missed.

    Phase 1: batch-classify suspicious claims (within domains, long, not split).
    Phase 2: decompose LLM-identified compounds via per-claim LLM calls.

    Returns (updated_claims, decomposed_details) — same shape as
    decompose_compound_general_claims for event compatibility.
    """
    # Phase 0: select candidates — in target domains, not already split, long enough
    candidates: list[tuple[int, dict[str, Any]]] = []
    for i, claim in enumerate(claims):
        if claim.get("domain", "general") not in domains:
            continue
        if claim.get("has_sub_claims") or claim.get("parent_statement_id"):
            continue
        text = (claim.get("text") or "").strip()
        if len(text) < _ATOMICITY_MIN_LENGTH:
            continue
        candidates.append((i, claim))

    if not candidates:
        return (claims, [])

    # Phase 1: batch-classify atomicity via single LLM call
    numbered = "\n".join(
        f"[{ci}] {c.get('text', '')}" for ci, (_, c) in enumerate(candidates)
    )
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
        logger.warning("Step '%s': atomicity gate classify failed: %s", step.id, e)
        return (claims, [])

    # Map candidate-local indices → real claim list indices
    compound_real: set[int] = set()
    for cand_idx, (real_idx, _) in enumerate(candidates):
        if cand_idx in compound_indices:
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
