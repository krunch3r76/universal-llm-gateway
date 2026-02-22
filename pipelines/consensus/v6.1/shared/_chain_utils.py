"""
Reusable decompose-verify-filter pipeline for chain enrichment.

Encapsulates the three-step (decompose → verify → filter) sequence that
runs at every link in the enrichment chain. Each function makes direct
LLM calls via BaseHandler utilities rather than instantiating full
pipeline steps.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from universal_logging import get_logger

from ._cascade import apply_cascade_rejection
from ._threshold import get_policy_fn

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.builtin import BaseHandler
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

# Sentence boundary: period/question/exclamation followed by whitespace (fixed-width).
# Abbreviations are re-joined in split_sentences() via _ends_with_abbreviation.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")
_ABBREVIATIONS = frozenset(
    {
        "dr.",
        "mr.",
        "ms.",
        "mrs.",
        "prof.",
        "jr.",
        "sr.",
        "vs.",
        "etc.",
        "approx.",
        "e.g.",
        "i.e.",
    }
)


def strip_json_fences(content: str) -> str:
    """Remove markdown code fences from start/end only.

    Some models (e.g. vision-capable) wrap JSON in ```json ... ``` even when
    response_format is json_object. Stripping at boundaries only avoids
    corrupting interior content (e.g. reasoning strings containing ```).
    """
    if not content or not content.strip():
        return content
    content = re.sub(r"^\s*```(?:json|JSON)?\s*\n?", "", content)
    content = re.sub(r"\n?```\s*$", "", content)
    return content.strip()


def token_budget(context: PipelineContext, key: str, fallback: int) -> int:
    """Read sub-category token budget from pipeline token_defaults.

    Falls back to *fallback* when the key is absent, preserving
    backward compatibility with pipelines that don't define sub-keys.
    """
    token_defaults = getattr(context.pipeline, "token_defaults", None)
    if token_defaults and key in token_defaults:
        return token_defaults[key]
    return fallback


# question_type → threshold policy (mirrors filter.py)
QUESTION_TYPE_POLICY: dict[str, str] = {
    "enumeration": "majority",
    "comparison": "majority",
    "definition": "majority",
    "explanation": "majority",
    "simple": "majority",
    "proof": "unanimous",
}


def _ends_with_abbreviation(text: str) -> bool:
    """Check if text ends with a known abbreviation."""
    lower = text.rstrip().lower()
    return any(lower.endswith(abbr) for abbr in _ABBREVIATIONS)


def split_sentences(text: str) -> list[str]:
    """Split *text* into sentences for provenance tracking."""
    raw = text.strip()
    if not raw:
        return []
    parts = [p.strip() for p in _SENTENCE_BOUNDARY_RE.split(raw) if p.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and _ends_with_abbreviation(merged[-1]):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


async def decompose_answer(
    handler: BaseHandler,
    answer_text: str,
    question: str,
    decompose_model_id: str,
    step: StepConfig,
    context: PipelineContext,
    prompt_ref: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Decompose answer text into atomic claims via one LLM call.

    Returns ``(claims, answer_sentences)`` where each claim dict carries
    ``source_sentences: list[int]`` referencing indices into the returned
    ``answer_sentences`` list.
    """
    sentences = split_sentences(answer_text)
    numbered_sentences = "\n".join(f"[{i}] {s}" for i, s in enumerate(sentences))

    rendered = handler._render_prompt(
        prompt_ref,
        {
            "question": question,
            "answer_text": answer_text,
            "numbered_sentences": numbered_sentences,
        },
        context,
        safe=True,
    )

    call_result = await handler._call_model(
        decompose_model_id,
        rendered.user_prompt,
        step,
        context,
        rendered.system_prompt,
        temperature=0.0,
        max_tokens=handler._constrained_tokens(
            token_budget(context, "verify_decompose", 2048), context
        ),
        call_label="decompose",
        json_schema={
            "type": "object",
            "properties": {
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "source_sentences": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                        },
                        "required": ["text", "source_sentences"],
                    },
                }
            },
            "required": ["claims"],
        },
    )

    if call_result.finish_reason == "length":
        logger.warning(
            "Chain decompose: model '%s' stopped due to length limit "
            "(tokens: %d prompt + %d completion). "
            "Response may be incomplete. Consider increasing verify_decompose token budget.",
            decompose_model_id,
            call_result.prompt_tokens,
            call_result.completion_tokens,
        )

    try:
        parsed = json.loads(strip_json_fences(call_result.content))
        claims_raw = parsed.get("claims", [])
    except json.JSONDecodeError as e:
        logger.error("Chain decompose: JSON parse failed: %s", e)
        return [], sentences

    max_idx = len(sentences) - 1
    claims: list[dict[str, Any]] = []
    for claim in claims_raw:
        if isinstance(claim, dict):
            text = str(claim.get("text", "")).strip()
            raw_indices = claim.get("source_sentences", [])
        else:
            # Graceful fallback: model returned a plain string
            text = str(claim).strip() if claim else ""
            raw_indices = []

        if not text:
            continue

        # Validate indices — clamp to valid range, drop out-of-range
        valid_indices = [
            i for i in raw_indices if isinstance(i, int) and 0 <= i <= max_idx
        ]
        if len(valid_indices) != len(raw_indices):
            logger.warning(
                "Chain decompose: claim has %d/%d valid sentence indices",
                len(valid_indices),
                len(raw_indices),
            )

        claims.append(
            {
                "statement_id": str(uuid4()),
                "text": text,
                "claim_type": "direct",
                "source_sentences": valid_indices,
            }
        )

    logger.info(
        "Chain decompose: extracted %d claims from %d sentences",
        len(claims),
        len(sentences),
    )
    return claims, sentences


async def contextualize_claims(
    handler: BaseHandler,
    claims: list[dict[str, Any]],
    question: str,
    answer_text: str,
    model_id: str,
    step: StepConfig,
    context: PipelineContext,
    prompt_ref: str,
    *,
    chunk_size: int | None = None,
) -> list[dict[str, Any]]:
    """Rewrite general-domain claims to be self-standing with topic anchoring.

    Skips math-domain claims (they are self-contained equations/formulas
    and rewriting risks subtle distortion). Must run after classify_claims.
    When chunk_size is set, processes general claims in batches of that size.
    On failure per batch, those claims remain unmodified (logged at ERROR).
    """
    general_indices = [
        i for i, c in enumerate(claims) if c.get("domain", "general") != "math"
    ]
    if not general_indices:
        logger.info("Chain contextualize: no general claims to contextualize")
        return claims

    general_claims = [claims[i] for i in general_indices]
    batch_size = (
        min(chunk_size, len(general_claims)) if chunk_size else len(general_claims)
    )
    rewritten_count = 0

    for batch_start in range(0, len(general_claims), batch_size):
        batch = general_claims[batch_start : batch_start + batch_size]
        batch_indices = general_indices[batch_start : batch_start + batch_size]

        # Use 0-based indexing for each batch (matches prompt's "0-based position")
        numbered_claims = "\n".join(
            f"[{i}] {c.get('text', '')}" for i, c in enumerate(batch)
        )
        rendered = handler._render_prompt(
            prompt_ref,
            {
                "claim_count": str(len(batch)),
                "cleaned_question": question,
                "answer_text": answer_text,
                "numbered_claims": numbered_claims,
            },
            context,
            safe=True,
        )

        call_result = await handler._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            rendered.system_prompt,
            temperature=0.0,
            max_tokens=handler._constrained_tokens(
                token_budget(context, "verify_contextualize", 2048), context
            ),
            call_label="contextualize",
            json_schema={
                "type": "object",
                "properties": {
                    "claims": {
                        "type": "array",
                        "minItems": len(batch),
                        "maxItems": len(batch),
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer"},
                                "claim": {"type": "string"},
                            },
                            "required": ["index", "claim"],
                        },
                    }
                },
                "required": ["claims"],
            },
        )

        try:
            parsed = json.loads(strip_json_fences(call_result.content))
            rewritten_raw = parsed.get("claims", [])
        except json.JSONDecodeError as e:
            logger.error(
                "Chain contextualize: JSON parse failed (batch %d): %s",
                batch_start,
                e,
            )
            continue

        if len(rewritten_raw) != len(batch):
            logger.error(
                "Chain contextualize: count mismatch in batch %d "
                "(expected %d, got %d), skipping",
                batch_start,
                len(batch),
                len(rewritten_raw),
            )
            continue

        # Build index→claim lookup for positional robustness
        rewritten_by_idx: dict[int, str] = {}
        for entry in rewritten_raw:
            if isinstance(entry, dict):
                idx = entry.get("index")
                claim = entry.get("claim", "")
                if isinstance(idx, int):
                    rewritten_by_idx[idx] = str(claim).strip()

        applied = 0
        for j, global_idx in enumerate(batch_indices):
            text = rewritten_by_idx.get(j, "")
            if text:
                claims[global_idx]["text"] = text
                applied += 1
        rewritten_count += applied

    math_count = len(claims) - len(general_indices)
    logger.info(
        "Chain contextualize: rewrote %d/%d general claims, skipped %d math claims",
        rewritten_count,
        len(general_indices),
        math_count,
    )
    return claims


async def classify_claims(
    handler: BaseHandler,
    claims: list[dict[str, Any]],
    classify_model_id: str,
    step: StepConfig,
    context: PipelineContext,
    prompt_ref: str,
    *,
    chunk_size: int | None = None,
) -> list[dict[str, Any]]:
    """Classify claims as math or general domain.

    Sets 'domain' field on each claim dict. If no classify prompt
    is configured, all claims default to 'general'.
    When chunk_size is set, processes claims in batches of that size.
    """
    if not prompt_ref:
        return claims

    batch_size = min(chunk_size, len(claims)) if chunk_size else len(claims)

    for start in range(0, len(claims), batch_size):
        batch = claims[start : start + batch_size]

        # Use 0-based indexing for each batch (matches prompt's "0-based position")
        numbered_statements = "\n".join(
            f"[{i}] {c.get('text', '')}" for i, c in enumerate(batch)
        )
        previous_context_lines: list[str] = []
        for i in range(len(batch)):
            global_idx = start + i
            if global_idx > 0:
                prev_text = claims[global_idx - 1].get("text", "")
                # Use batch-local index in previous context
                previous_context_lines.append(
                    f"[{i}]: previous statement was from global position {global_idx - 1}: {prev_text}"
                )
        previous_context = (
            "\n".join(previous_context_lines)
            if previous_context_lines
            else "(none - first statement)"
        )

        rendered = handler._render_prompt(
            prompt_ref,
            {
                "count": str(len(batch)),
                "statements": numbered_statements,
                "previous_context": previous_context,
            },
            context,
            safe=True,
        )

        try:
            call_result = await handler._call_model(
                classify_model_id,
                rendered.user_prompt,
                step,
                context,
                system_prompt=rendered.system_prompt,
                temperature=0.0,
                max_tokens=handler._constrained_tokens(
                    token_budget(context, "verify_classify", 1024), context
                ),
                call_label="classify",
                json_schema={
                    "type": "object",
                    "properties": {
                        "classifications": {
                            "type": "array",
                            "minItems": len(batch),
                            "maxItems": len(batch),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "index": {"type": "integer"},
                                    "domain": {
                                        "type": "string",
                                        "enum": ["math", "general"],
                                    },
                                    "reasoning": {"type": "string"},
                                },
                                "required": ["index", "domain"],
                            },
                        }
                    },
                    "required": ["classifications"],
                },
            )
            parsed = json.loads(call_result.content)
            classifications = parsed.get("classifications", [])
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(
                "Step '%s': classify JSON parse failed (batch %d): %s",
                step.id,
                start,
                e,
            )
            continue
        except Exception as e:
            logger.error(
                "Step '%s': claim classification failed (batch %d): %s",
                step.id,
                start,
                e,
                exc_info=True,
            )
            continue

        for entry in classifications:
            if not isinstance(entry, dict):
                raise TypeError(
                    f"Step '{step.id}': malformed classification entry - "
                    f"expected dict, got {type(entry).__name__}: {entry!r}"
                )
            batch_idx = entry.get("index")
            domain = entry.get("domain", "general")
            # Map batch-local index to global index
            if isinstance(batch_idx, int) and 0 <= batch_idx < len(batch):
                global_idx = start + batch_idx
                claims[global_idx]["domain"] = domain

    math_count = sum(1 for c in claims if c.get("domain") == "math")
    if math_count:
        logger.info(
            "Step '%s': classified %d/%d claims as math",
            step.id,
            math_count,
            len(claims),
        )
    return claims


def format_numbered_facts(
    facts: list[dict[str, Any]],
    *,
    start_index: int = 1,
) -> str:
    """Format verified facts as numbered list, grouped by context_prefix when present.

    When any fact has context_prefix, output is grouped by topic with
    ``CONTEXT: <topic>`` labels and sequential numbers across groups.
    Topics are case-normalized (title case) so that e.g. "Metformin" and
    "metformin" merge into one group. Facts without context_prefix appear
    in a final ungrouped section. When no facts have context_prefix, output
    is a flat numbered list (backward compatible with v6.0).
    """
    if not facts:
        return ""

    has_prefix = any(
        (f.get("context_prefix") or "").strip() for f in facts if isinstance(f, dict)
    )
    if not has_prefix:
        lines = []
        for i, fact in enumerate(facts, start=start_index):
            text = (
                fact.get("text", str(fact)).strip()
                if isinstance(fact, dict)
                else str(fact).strip()
            )
            if text:
                lines.append(f"[{i}] {text}")
        return "\n".join(lines)

    # Partition into (normalized_topic, [facts]) preserving insertion order
    order: list[str] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    ungrouped: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        text = (fact.get("text") or str(fact)).strip()
        if not text:
            continue
        raw_topic = (fact.get("context_prefix") or "").strip()
        if raw_topic:
            topic = raw_topic.title()
            if topic not in groups:
                order.append(topic)
                groups[topic] = []
            groups[topic].append(fact)
        else:
            ungrouped.append(fact)

    lines: list[str] = []
    idx = start_index
    for topic in order:
        lines.append(f"CONTEXT: {topic}")
        for fact in groups[topic]:
            text = (fact.get("text") or "").strip()
            if text:
                lines.append(f"[{idx}] {text}")
                idx += 1
    for fact in ungrouped:
        text = (fact.get("text") or "").strip()
        if text:
            lines.append(f"[{idx}] {text}")
            idx += 1
    return "\n".join(lines)


def find_borderline_claims(
    candidates: list[dict[str, Any]],
    verdicts: dict[str, list[bool]],
    policy_name: str,
    *,
    include_accepted_borderline: bool = False,
) -> list[dict[str, Any]]:
    """Identify claims within 1 vote of the consensus threshold.

    Default: only rejected claims (votes_for == threshold - 1) where a
    single additional "yes" vote would flip the outcome to accepted.

    With include_accepted_borderline=True, also includes barely-accepted
    claims (votes_for == threshold) that could be flipped by a "no" vote.

    Works for any pool size N — threshold adjusts via the policy function.
    """
    policy_fn = get_policy_fn(policy_name)
    borderline: list[dict[str, Any]] = []
    for candidate in candidates:
        sid = candidate.get("statement_id", "")
        votes = verdicts.get(sid, [])
        if not votes:
            continue
        threshold = policy_fn(len(votes))
        votes_for = sum(1 for v in votes if v)
        # Rejected by 1 vote — tiebreaker can rescue
        if votes_for == threshold - 1:
            borderline.append(candidate)
        # Accepted at threshold — tiebreaker can only reinforce or veto
        elif include_accepted_borderline and votes_for == threshold:
            borderline.append(candidate)
    return borderline


def filter_claims(
    candidates: list[dict[str, Any]],
    verdicts: dict[str, list[bool]],
    question_type: str,
    verification_policy: str = "majority",
    math_verification_policy: str = "unanimous_reject",
) -> list[dict[str, Any]]:
    """
    Apply threshold filtering and cascade rejection.

    Args:
        candidates: List of claim dicts with statement_id, domain, etc.
        verdicts: statement_id -> list of True/False votes
        question_type: Question type (for fallback policy)
        verification_policy: Default policy (majority, unanimous, 2/3_majority, 1/3_present, unanimous_reject)
        math_verification_policy: Policy for math/proof domain (unanimous_reject = veto only when all False)

    Returns list of accepted claim dicts.
    """
    passed: dict[str, bool] = {}
    for c in candidates:
        sid = c.get("statement_id", "")
        votes = verdicts.get(sid, [])
        true_count = sum(1 for v in votes if v)

        domain = c.get("domain", "general")
        policy_name = (
            math_verification_policy if domain == "math" else verification_policy
        )
        policy_fn = get_policy_fn(policy_name)
        required = policy_fn(len(votes)) if votes else 1
        passed[sid] = true_count >= required

    # Cascade rejection
    passed_list = [c for c in candidates if passed.get(c.get("statement_id", ""))]
    passed_copy = [dict(c) for c in passed_list]
    apply_cascade_rejection(passed_copy)
    accepted_ids = {c["statement_id"] for c in passed_copy}

    # Orphan filtering: remove verified claims whose parent was rejected
    # (skip subclaims whose parent is excluded, e.g. compound parents)
    candidate_ids = {c.get("statement_id") for c in candidates}
    orphaned_ids = set()
    for c in passed_copy:
        parent_id = c.get("parent_statement_id")
        if parent_id and parent_id in candidate_ids and parent_id not in accepted_ids:
            orphaned_ids.add(c["statement_id"])
            logger.info(
                "Chain filter: orphaned claim (parent rejected): %s",
                c.get("text", "")[:100],
            )

    accepted_ids -= orphaned_ids

    accepted = [c for c in candidates if c.get("statement_id") in accepted_ids]
    logger.info(
        "Chain filter: %d accepted, %d rejected (%d orphaned)",
        len(accepted),
        len(candidates) - len(accepted),
        len(orphaned_ids),
    )
    return accepted
