"""Predicate-form aggregation for Card v0 predicate_summary slot (v2.4 §6.7 / §5.5.6).

Three-tier aggregation strategy (Slice 4 cap-at-1 ratification, thread 907):

  Tier 0  mechanical join of predicate_forms already populated in top-K rows.
          Join order = card top-K query order (summary-first, pointer-last,
          created_at DESC). Order is contract-stable for §5.5.4 cognitive
          cache hashing — any future reorder must coordinate with cache-key
          derivation.

  Tier 1  opportunistic sync enrichment, capped at AT MOST ONE missing
          predicate_form per card read. Protects card-read latency: T1 is
          sub-second per inference; without the cap N=3 missing assertions
          blow the ~500ms budget by 2-3×. Enabled only when
          CORTEX_PREDICATE_SYNC_MODEL is set. Failures are swallowed.

  Tier 2  edge-derived heuristic fallback via synthesize_predicate_summary()
          (et_type_counts + archives_to_children, no claim-text inspection).
          §6.7 scope-narrow: deterministic-rule-extraction-over-assertion-text
          deferred to Phase 2/5 (assertion 8866 on spec:cortex-v2.4).
          Fires only when zero predicate_forms remain after Tier 0 + Tier 1.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from universal_logging import get_logger

from .compaction import synthesize_predicate_summary

logger = get_logger("cortex-api.predicate_summary")

STARGATE_URL = os.environ.get("STARGATE_URL", "http://localhost:9999")
_SYNC_MODEL = os.environ.get("CORTEX_PREDICATE_SYNC_MODEL", "")
_SYNC_TIMEOUT = float(os.environ.get("CORTEX_PREDICATE_SYNC_TIMEOUT_S", "1.5"))

# Mirrors prompts.yaml system_prompt for the predicate_extract pipeline.
# Kept in sync manually; the pipeline YAML is the authoritative definition.
_SYSTEM_PROMPT = """\
You convert natural-language claims into compact predicate-form \
expressions used for first-order-logic-bag (FOL-B) retrieval.

Output format (single line, no prose, no markdown, no quotes):
  predicate(subject, object[, modifier ...])

Rules:
- Output exactly one line. Use exactly one predicate.
- Use snake_case for the predicate name.
- Subject and object refer to concrete entities or noun phrases.
- Modifiers (optional) carry temporal, modal, polarity, or qualifier info.
- Drop hedges, narration, and citation-style scaffolding.
- Prefer a relational predicate whenever any relation can be inferred.
- Use describes(entity_id, short_noun_phrase) only as a last resort \
when no plausible binary relation can be formed.
- Never explain."""


def _tier1_infer(claim: str, entity_id: str) -> str | None:
    """Sync LLM call to infer one predicate_form (Tier 1, §5.5.6 T1).

    Bounded by _SYNC_TIMEOUT. Returns None on any failure — callers must
    handle gracefully. ¬ raises. Skipped immediately when _SYNC_MODEL
    is unset.
    """
    if not _SYNC_MODEL:
        return None

    user_prompt = (
        f"Entity: {entity_id}\nClaim: {claim}\n\n"
        "Predicate form (prefer a relation anchored on the claim subject; "
        "use describes only if no binary relation is possible):"
    )
    payload: dict[str, Any] = {
        "model": _SYNC_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 128,
        "temperature": 0.1,
    }
    try:
        with httpx.Client(timeout=_SYNC_TIMEOUT) as client:
            resp = client.post(f"{STARGATE_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            for line in content.splitlines():
                stripped = line.strip()
                if stripped:
                    return stripped
    except Exception:
        logger.debug(
            "Tier 1 predicate_form inference failed (claim=%r)",
            claim[:60],
            exc_info=True,
        )
    return None


def aggregate_predicate_summary(
    top_k_assertions: list[dict[str, Any]],
    et_type_counts: list[dict[str, Any]],
    archives_to_children: list[str],
    *,
    entity_id: str | None = None,
) -> str:
    """Aggregate predicate_forms from top-K assertions into a predicate_summary string.

    ∀ r ∈ top_k_assertions: r has at minimum keys ``id``, ``claim``,
    ``predicate_form`` (may be None for unenriched assertions).

    Join order = card top-K query order. Do not sort or reorder: the join
    is contract-stable for §5.5.4 cognitive cache hashing.

    *entity_id* is passed to the Tier 1 LLM prompt for context; "unknown"
    is used when omitted (graceful degradation, not a hard requirement).

    Returns empty string when no signal available — never None.
    """
    forms: list[str] = []
    tier1_used = False

    for row in top_k_assertions:
        pf = row.get("predicate_form")
        if pf:
            forms.append(str(pf))
        elif not tier1_used:
            # Tier 1: attempt sync enrichment for the first missing row only.
            # ∃! sync call per card read — cap enforced by tier1_used flag.
            inferred = _tier1_infer(str(row.get("claim") or ""), entity_id or "unknown")
            if inferred:
                forms.append(inferred)
            tier1_used = True
            # ∀ remaining misses: contribute nothing to join (Tier 2 partial).

    if forms:
        return "; ".join(forms)

    # Tier 2: zero predicate_forms available after Tier 0 + Tier 1.
    # Edge-only heuristic — no claim-text inspection (§6.7 scope-narrow).
    return synthesize_predicate_summary(
        et_type_counts=et_type_counts,
        archives_to_children=archives_to_children,
    )
