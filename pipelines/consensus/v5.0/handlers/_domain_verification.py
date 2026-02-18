"""
Domain-specific authority verification for chain enrichment.

Routes claims to domain authority models (e.g., math → zyphra_math).
Patterns extracted from v3.3 ConsensusDomainSpecificVerificationHandler;
no cross-version imports.

Invariants:
    ∀ verified stmt: originator_model_id ≠ authority_model_id (provenance)
    ∀ authority call: isolated request (1 claim = 1 request)
    ∀ authority call: parallelized via parallel_model_calls()
    ∀ config error: raise immediately (not permissive)
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from provenance import extract_provenance, is_independent
from systems.pipeline.core.handlers import parallel_model_calls
from universal_logging import get_logger

from ._chain_utils import token_budget

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.builtin import BaseHandler
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


def extract_boxed_verdict(content: str) -> bool | None:
    """
    Extract verdict from LaTeX \\boxed{} pattern.

    Parses free-form model output for \\boxed{T}/\\boxed{TRUE} (True) or
    \\boxed{F}/\\boxed{FALSE} (False). Used by math domain verification
    requiring LaTeX formatting.

    Returns:
        True if \\boxed{T} or \\boxed{TRUE}, False if \\boxed{F} or \\boxed{FALSE},
        None if pattern not found.
    """
    if not content:
        return None

    match = re.search(r"\\boxed\{(TRUE|FALSE|T|F)\}", content, re.IGNORECASE)
    if not match:
        return None

    verdict_str = match.group(1).upper()
    return verdict_str in ("TRUE", "T")


async def apply_domain_verification(
    handler: BaseHandler,
    claims: list[dict[str, Any]],
    step: StepConfig,
    context: PipelineContext,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """
    Route domain-specific claims to authority models.

    Two-tier verification:
    1. Group claims by domain, match against domain_verifiers config
    2. Per domain: provenance-filter, then verify in parallel
    3. Apply veto policy to partition into general-eligible vs final

    Authority verdict mapping:
        FALSE → claim rejected (skip general verification)
        TRUE + veto disabled → claim accepted immediately (final=True)
        TRUE + veto enabled → claim passes to general verification

    Returns:
        claims_for_general: Claims that need general verification
        authority_verdicts: {statement_id: {verdict, domain, authority_model, final?}}
    """
    domain_config = step.get_domain_field("domain_verification")
    if not domain_config or not domain_config.get("enabled"):
        return claims, {}

    domain_verifiers = domain_config.get("domain_verifiers", {})
    authority_verdicts: dict[str, dict[str, Any]] = {}
    claims_for_general: list[dict[str, Any]] = []

    # Group claims by domain
    domain_groups: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        domain = claim.get("domain", "general")
        if domain in domain_verifiers:
            domain_groups.setdefault(domain, []).append(claim)
        else:
            claims_for_general.append(claim)

    # Process each domain with authority verification
    for domain, domain_claims in domain_groups.items():
        verifier = domain_verifiers[domain]

        model_ref = verifier.get("model_ref")
        if not model_ref:
            raise ValueError(
                f"Step '{step.id}': domain_verifiers['{domain}'] missing model_ref"
            )

        model_id = handler._resolve_model_alias(model_ref, context)

        # Provenance filter: originator ≠ authority model
        eligible = _filter_by_provenance(domain_claims, model_id, step.id)
        skipped = len(domain_claims) - len(eligible)
        if skipped:
            logger.info(
                "Step '%s': domain '%s', excluded %d/%d claims by provenance",
                step.id,
                domain,
                skipped,
                len(domain_claims),
            )
            # Provenance-excluded claims go to general verification
            eligible_ids = {c.get("statement_id") for c in eligible}
            for c in domain_claims:
                if c.get("statement_id") not in eligible_ids:
                    claims_for_general.append(c)

        if not eligible:
            continue

        # Verify batch in parallel
        batch_verdicts = await _verify_domain_batch(
            handler=handler,
            claims=eligible,
            verifier_config=verifier,
            model_id=model_id,
            step=step,
            context=context,
        )

        # Apply veto policy
        veto_enabled = verifier.get("veto_policy", {}).get("enabled", True)

        for claim in eligible:
            sid = claim.get("statement_id", "")
            verdict = batch_verdicts.get(sid, True)

            if not verdict:
                authority_verdicts[sid] = {
                    "verdict": False,
                    "domain": domain,
                    "authority_model": model_ref,
                }
                logger.info(
                    "Step '%s': authority REJECTED %s claim: %s",
                    step.id,
                    domain,
                    claim.get("text", "")[:80],
                )
            elif veto_enabled:
                claims_for_general.append(claim)
                authority_verdicts[sid] = {
                    "verdict": True,
                    "domain": domain,
                    "authority_model": model_ref,
                }
            else:
                authority_verdicts[sid] = {
                    "verdict": True,
                    "domain": domain,
                    "authority_model": model_ref,
                    "final": True,
                }

    return claims_for_general, authority_verdicts


def _filter_by_provenance(
    claims: list[dict[str, Any]],
    authority_model_id: str,
    step_id: str,
) -> list[dict[str, Any]]:
    """
    Filter out claims originated by the authority model.

    Invariant: ∀ verified stmt: originator_model_id ≠ authority_model_id
    On provenance check failure: include claim (prioritize availability).
    """
    eligible: list[dict[str, Any]] = []
    for claim in claims:
        try:
            prov = extract_provenance(claim)
            if prov is None or is_independent(prov, authority_model_id):
                eligible.append(claim)
        except Exception as e:
            logger.warning(
                "Step '%s': provenance check failed for %s: %s. Including by default.",
                step_id,
                claim.get("statement_id", "unknown"),
                e,
            )
            eligible.append(claim)
    return eligible


async def _verify_domain_batch(
    handler: BaseHandler,
    claims: list[dict[str, Any]],
    verifier_config: dict[str, Any],
    model_id: str,
    step: StepConfig,
    context: PipelineContext,
) -> dict[str, bool]:
    """
    Verify all claims for one domain concurrently via parallel_model_calls.

    Each claim becomes one isolated LLM request (serial_parallel pattern).
    Uses existing parallel_model_calls utility for max_concurrency + error handling.
    """
    prompt_ref = verifier_config.get("prompt_ref")
    if not prompt_ref:
        raise ValueError(f"Step '{step.id}': domain verifier missing prompt_ref")

    gen_params = verifier_config.get("generation_parameters", {})
    results: dict[str, bool] = {}

    # Build index for prev_sentence lookup
    claims_by_id = {c.get("statement_id"): (i, c) for i, c in enumerate(claims)}

    async def verify_single(claim: dict[str, Any]) -> dict[str, Any] | None:
        sid = claim.get("statement_id", "")
        claim_text = claim.get("text", "")

        # Get previous claim for disambiguation (if exists)
        idx, _ = claims_by_id.get(sid, (0, None))
        prev_sentence = claims[idx - 1].get("text", "") if idx > 0 else ""

        rendered = handler._render_prompt(
            prompt_ref,
            {
                "statement": claim_text,
                "claim": claim_text,
                "prev_sentence": prev_sentence,
            },
            context,
            safe=True,
        )
        try:
            call_result = await handler._call_model(
                model_id,
                rendered.user_prompt,
                step,
                context,
                system_prompt=rendered.system_prompt,
                temperature=gen_params.get("temperature", 0.7),
                max_tokens=handler._constrained_tokens(
                    token_budget(context, "verify_domain", 512), context
                ),
                call_label="domain_verify",
            )
            verdict = extract_boxed_verdict(call_result.content)
            results[sid] = verdict is True
        except Exception as e:
            logger.error(
                "Step '%s': authority verification failed for claim %s: %s",
                step.id,
                sid,
                e,
            )
            # Permissive: authority failure → claim passes to general
            results[sid] = True
        return {"statement_id": sid}

    await parallel_model_calls(
        claims,
        verify_single,
        description=f"domain_verify_{step.id}",
    )

    return results


def merge_authority_verdicts(
    all_claims: list[dict[str, Any]],
    general_accepted: list[dict[str, Any]],
    authority_verdicts: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Merge authority verdicts with general verification results.

    Authority outcomes:
        verdict=False → rejected_claims
        verdict=True, final=True → verified_facts (authority-only acceptance)
        verdict=True, final=False → already in general flow, general result decides

    General outcomes (for claims without authority verdict):
        In general_accepted → verified_facts
        Not in general_accepted → rejected_claims

    Returns:
        (verified_facts, rejected_claims) — complete partition of all_claims
    """
    general_accepted_ids = {c.get("statement_id") for c in general_accepted}
    verified: list[dict[str, Any]] = list(general_accepted)
    rejected: list[dict[str, Any]] = []

    for claim in all_claims:
        sid = claim.get("statement_id", "")
        auth = authority_verdicts.get(sid)

        if not auth:
            # No authority verdict → purely general result
            if sid not in general_accepted_ids:
                rejected.append(claim)
            continue

        if not auth["verdict"]:
            # Authority rejected
            rejected.append(claim)
        elif auth.get("final"):
            # Authority accepted, final → verified (no general needed)
            verified.append(claim)
        else:
            # Authority accepted, not final → general result decides
            if sid not in general_accepted_ids:
                rejected.append(claim)

    # Orphan filtering: remove verified claims whose non-compound parent was
    # rejected (compound parent subclaims derive individual verdicts, not cascaded)
    compound_parent_ids = {
        c.get("statement_id") for c in all_claims if c.get("has_sub_claims")
    }
    verified_ids = {c["statement_id"] for c in verified}
    orphaned = []
    for claim in verified:
        parent_id = claim.get("parent_statement_id")
        if (
            parent_id
            and parent_id not in verified_ids
            and parent_id not in compound_parent_ids
        ):
            orphaned.append(claim)
            rejected.append(claim)

    if orphaned:
        from universal_logging import get_logger

        logger = get_logger(__name__)
        for claim in orphaned:
            logger.info(
                "Domain merge: orphaned claim (parent rejected): %s",
                claim.get("text", "")[:100],
            )
        verified = [c for c in verified if c not in orphaned]

    return verified, rejected
