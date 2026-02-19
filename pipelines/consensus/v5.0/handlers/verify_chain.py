"""
Atomic verify step: decompose → classify → verify → filter.

Single pipeline step that encapsulates the full verification pipeline.
Reusable across chain links — each link is one step in YAML.

Invariants:
    ∀ claim ∈ verified_facts: passed consensus threshold
    ∀ claim ∈ rejected_claims: failed consensus threshold ∨ cascade-rejected
    verified_facts ∪ rejected_claims = all_decomposed_claims
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, override

from provenance.cross_model import order_models_by_affinity
from systems.pipeline.core.events.verification import (
    ClaimsClassified,
    ClaimsContextualized,
    ClaimsExtracted,
    CompoundClaimsDecomposed,
    DomainVerificationCompleted,
    ModelVerdictCast,
    ThresholdApplied,
    TiebreakerTriggered,
    VerificationComplete,
)
from systems.pipeline.core.execution.chunked import (
    ModelExecutionConfig,
    get_execution_config,
)
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ._chain_utils import (
    classify_claims,
    contextualize_claims,
    decompose_answer,
    filter_claims,
    find_borderline_claims,
)
from ._chain_verification import verify_claims
from ._domain_verification import apply_domain_verification, merge_authority_verdicts
from .v4_types import VerdictEntry

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class VerifyChainHandler(BaseHandler):
    """
    Atomic verification unit: decompose + verify + filter.

    Decomposes answer into claims, classifies by domain,
    runs verification across multiple models, filters by
    consensus threshold.

    Domain verification (math authority, etc.) is an optional
    extension — when domain_verification.enabled is true,
    domain-specific claims are routed to authority models
    before general consensus.

    Output stats: total_claims, accepted, rejected, latency_ms,
    decompose_latency_ms, classify_latency_ms, verification_timing
    (per-model and per-chunk latency breakdown).
    """

    step_type: str = "consensus_verify_chain_v4"

    def _emit(self, context: PipelineContext, event: Any) -> None:
        """Emit event to recorder if available."""
        recorder = context.recorder
        if recorder:
            recorder.emit(event)

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Run atomic verify: decompose → classify → verify → filter."""
        start_time = time.time()

        # 1. Resolve inputs
        resolver = NamespaceResolver(context)
        answer = str(
            self._resolve_input(resolver, step, "answer", step.handler_inputs) or ""
        )
        question = str(
            self._resolve_input(resolver, step, "question", step.handler_inputs) or ""
        )
        question_type = str(
            self._resolve_input(resolver, step, "question_type", step.handler_inputs)
            or "general"
        )
        originator = self._resolve_input(
            resolver, step, "originator", step.handler_inputs
        )
        originator = str(originator) if originator is not None else None

        if not answer:
            logger.error("Step '%s': empty answer input", step.id)
            return StepOutput(raw="", json={"error": "empty answer"})

        # Resolve config from domain fields
        verify_model_aliases = self._resolve_verify_models(step, context, originator)

        # Order by affinity: answer-pool models first (likely loaded), others last
        answer_models_opt = (context.options or {}).get("answer_models", {})
        answer_pool: set[str] = (
            set(answer_models_opt.values())
            if isinstance(answer_models_opt, dict)
            else set(answer_models_opt)
            if isinstance(answer_models_opt, list)
            else set()
        )
        if answer_pool:
            verify_model_aliases = order_models_by_affinity(
                verify_model_aliases, answer_pool
            )

        # Full verifier pool (before exclude_self) — for event metadata
        _pool_raw = step.get_domain_field("model_pool")
        if isinstance(_pool_raw, list):
            verifier_pool_aliases: list[str] = list(_pool_raw)
        elif isinstance(_pool_raw, str):
            _opts = context.options or {}
            _resolved = _opts.get(_pool_raw.split(".")[-1], [])
            verifier_pool_aliases = (
                list(_resolved)
                if isinstance(_resolved, list)
                else list(verify_model_aliases)
            )
        else:
            verifier_pool_aliases = list(verify_model_aliases)
        verify_model_ids = [
            self._resolve_model_alias(alias, context) for alias in verify_model_aliases
        ]
        registry = context._registry
        exec_configs: dict[str, ModelExecutionConfig] = {}
        # Pipeline sets desired chunk size; model execution.chunk_size caps it
        verification_chunk_size = step.get_domain_field("verification_chunk_size")
        if verification_chunk_size is None:
            verification_chunk_size = context.pipeline.options.get(
                "verification_chunk_size"
            )
        for alias in verify_model_aliases:
            model_config = registry.get_model_config(
                alias, domain=context.pipeline.domain
            )
            exec_config = get_execution_config(model_config)
            if verification_chunk_size is not None:
                exec_config.chunk_size = min(
                    int(verification_chunk_size), exec_config.chunk_size
                )
            exec_configs[model_config.model] = exec_config
        prompt_ref_verify_batch = step.get_domain_field("prompt_ref_verify_batch")
        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")
        decompose_model_id = self._resolve_model_alias(step.model_ref, context)

        # Decompose model capability (caps classify/contextualize chunk sizes)
        decompose_model_config = registry.get_model_config(
            step.model_ref, domain=context.pipeline.domain
        )
        decompose_exec = get_execution_config(decompose_model_config)

        prompt_ref_decompose = self._require_domain_field(step, "prompt_ref_decompose")
        prompt_ref_verify = self._require_domain_field(step, "prompt_ref_verify")
        prompt_ref_classify = str(step.get_domain_field("prompt_ref_classify") or "")

        verification_policy = step.get_domain_field("verification_policy")
        if not verification_policy:
            verification_policy = "majority"
            logger.error(
                "Step '%s': no verification_policy configured, using default '%s'",
                step.id,
                verification_policy,
            )
        verification_policy = str(verification_policy)

        math_verification_policy = step.get_domain_field("math_verification_policy")
        if not math_verification_policy:
            math_verification_policy = "unanimous_reject"
            logger.error(
                "Step '%s': no math_verification_policy configured, using default '%s'",
                step.id,
                math_verification_policy,
            )
        math_verification_policy = str(math_verification_policy)

        verify_chunk_summary = ", ".join(
            f"{mid}={cfg.chunk_size}" for mid, cfg in exec_configs.items()
        )
        logger.info(
            "Step '%s': verify chain — %d verify models, policy=%s, math_policy=%s, "
            "decompose_model_cap=%d, verify_chunks=[%s]",
            step.id,
            len(verify_model_ids),
            verification_policy,
            math_verification_policy,
            decompose_exec.chunk_size,
            verify_chunk_summary,
        )

        # 2. Decompose answer into claims (with sentence provenance)
        decompose_start = time.time()
        claims, answer_sentences = await decompose_answer(
            handler=self,
            answer_text=answer,
            question=question,
            decompose_model_id=decompose_model_id,
            step=step,
            context=context,
            prompt_ref=prompt_ref_decompose,
        )
        decompose_latency_ms = (time.time() - decompose_start) * 1000

        # Event: claims extracted from answer
        self._emit(
            context,
            ClaimsExtracted(
                step_name=step.id,
                claims=[
                    {
                        "statement_id": c.get("statement_id", ""),
                        "text": c.get("text", ""),
                        "source_sentences": c.get("source_sentences", []),
                    }
                    for c in claims
                ],
                source_step=str(step.handler_inputs.get("answer", ""))
                if step.handler_inputs
                else "",
                decompose_latency_ms=round(decompose_latency_ms, 2),
                answer_sentences=answer_sentences,
            ),
        )

        if not claims:
            latency_ms = (time.time() - start_time) * 1000
            return StepOutput(
                raw="",
                json={
                    "verified_facts": [],
                    "rejected_claims": [],
                    "authority_verdicts": {},
                    "stats": {
                        "total_claims": 0,
                        "accepted": 0,
                        "rejected": 0,
                        "latency_ms": latency_ms,
                        "decompose_latency_ms": round(decompose_latency_ms, 2),
                        "contextualize_latency_ms": 0.0,
                    },
                },
                latency_ms=latency_ms,
            )

        # 3. Classify claims by domain (before contextualize so math claims are excluded)
        classify_chunk_size = step.get_domain_field("classify_chunk_size")
        if classify_chunk_size is None:
            classify_chunk_size = context.pipeline.options.get("classify_chunk_size")
        effective_classify_chunk = (
            min(int(classify_chunk_size), decompose_exec.chunk_size)
            if classify_chunk_size is not None
            else decompose_exec.chunk_size
        )
        logger.info(
            "Step '%s': classify chunk_size=%d (pipeline=%s, model_cap=%d)",
            step.id,
            effective_classify_chunk,
            classify_chunk_size,
            decompose_exec.chunk_size,
        )
        classify_start = time.time()
        claims = await classify_claims(
            self,
            claims,
            decompose_model_id,
            step,
            context,
            prompt_ref_classify,
            chunk_size=effective_classify_chunk,
        )
        classify_latency_ms = (time.time() - classify_start) * 1000

        # Event: claims classified by domain
        domain_counts: dict[str, int] = {}
        classifications: dict[str, str] = {}
        for c in claims:
            domain = c.get("domain", "general")
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
            classifications[c.get("statement_id", "")] = domain
        self._emit(
            context,
            ClaimsClassified(
                step_name=step.id,
                classifications=classifications,
                domain_counts=domain_counts,
                classify_latency_ms=round(classify_latency_ms, 2),
            ),
        )

        # 3b. Contextualize general claims (optional — makes claims self-standing for batch verify)
        contextualize_enabled = step.get_domain_field("contextualize_claims", False)
        if contextualize_enabled:
            prompt_ref_ctx = str(
                step.get_domain_field("prompt_ref_contextualize")
                or "consensus.v4.0.contextualize_claims"
            )
            ctx_chunk_size = step.get_domain_field("contextualize_chunk_size")
            if ctx_chunk_size is None:
                ctx_chunk_size = context.pipeline.options.get(
                    "contextualize_chunk_size"
                )
            effective_ctx_chunk = (
                min(int(ctx_chunk_size), decompose_exec.chunk_size)
                if ctx_chunk_size is not None
                else decompose_exec.chunk_size
            )
            logger.info(
                "Step '%s': contextualize chunk_size=%d (pipeline=%s, model_cap=%d)",
                step.id,
                effective_ctx_chunk,
                ctx_chunk_size,
                decompose_exec.chunk_size,
            )
            ctx_start = time.time()
            claims = await contextualize_claims(
                self,
                claims,
                question,
                answer,
                decompose_model_id,
                step,
                context,
                prompt_ref_ctx,
                chunk_size=effective_ctx_chunk,
            )
            contextualize_latency_ms = (time.time() - ctx_start) * 1000
            # Event: claims contextualized
            math_count = sum(1 for c in claims if c.get("domain") == "math")
            self._emit(
                context,
                ClaimsContextualized(
                    step_name=step.id,
                    rewritten_count=len(claims) - math_count,
                    skipped_count=math_count,
                    contextualize_latency_ms=round(contextualize_latency_ms, 2),
                ),
            )
        else:
            contextualize_latency_ms = 0.0

        # 3c. Decompose compound claims (on by default for all verify steps)
        decompose_compound = step.get_domain_field("decompose_compound_general", True)
        decompose_compound_math = step.get_domain_field("decompose_compound_math", True)
        if decompose_compound or decompose_compound_math:
            from ._decompose_compound import decompose_compound_general_claims

            compound_domains: set[str] = set()
            if decompose_compound:
                compound_domains.add("general")
            if decompose_compound_math:
                compound_domains.add("math")

            prompt_ref_compound = str(
                step.get_domain_field("prompt_ref_decompose_compound")
                or "consensus.v4.0.decompose_general_compound"
            )
            compound_start = time.time()
            claims, compound_details = await decompose_compound_general_claims(
                handler=self,
                claims=claims,
                model_id=decompose_model_id,
                step=step,
                context=context,
                prompt_ref=prompt_ref_compound,
                domains=frozenset(compound_domains),
            )
            compound_latency_ms = (time.time() - compound_start) * 1000

            if compound_details:
                self._emit(
                    context,
                    CompoundClaimsDecomposed(
                        step_name=step.id,
                        decomposed_count=len(compound_details),
                        total_sub_claims=sum(
                            len(d["sub_claims"]) for d in compound_details
                        ),
                        decompose_latency_ms=round(compound_latency_ms, 2),
                        details=compound_details,
                    ),
                )

        # 3d. Atomicity gate: LLM-based compound detection for heuristic misses
        atomicity_gate = step.get_domain_field("atomicity_gate", True)
        if atomicity_gate:
            from ._decompose_compound import atomicity_gate_decompose

            # Gate applies to same domains as heuristic decomposition
            gate_domains: set[str] = {"general"}
            if decompose_compound_math:
                gate_domains.add("math")

            prompt_ref_atom_classify = str(
                step.get_domain_field("prompt_ref_atomicity_classify")
                or "consensus.v4.0.classify_atomicity"
            )
            prompt_ref_atom_decompose = str(
                step.get_domain_field("prompt_ref_decompose_compound")
                or "consensus.v4.0.decompose_general_compound"
            )
            atom_start = time.time()
            claims, atom_details = await atomicity_gate_decompose(
                handler=self,
                claims=claims,
                model_id=decompose_model_id,
                step=step,
                context=context,
                prompt_ref_classify=prompt_ref_atom_classify,
                prompt_ref_decompose=prompt_ref_atom_decompose,
                domains=frozenset(gate_domains),
            )
            atom_latency_ms = (time.time() - atom_start) * 1000

            if atom_details:
                self._emit(
                    context,
                    CompoundClaimsDecomposed(
                        step_name=step.id,
                        decomposed_count=len(atom_details),
                        total_sub_claims=sum(
                            len(d["sub_claims"]) for d in atom_details
                        ),
                        decompose_latency_ms=round(atom_latency_ms, 2),
                        details=atom_details,
                    ),
                )

        # Exclude compound parents from direct verification — verdict derived
        # from subclaims (compound claims are hard for LLMs to verify directly)
        compound_parents = [c for c in claims if c.get("has_sub_claims")]
        verifiable_claims = [c for c in claims if not c.get("has_sub_claims")]
        if compound_parents:
            logger.info(
                "Step '%s': excluded %d compound parents from direct verification",
                step.id,
                len(compound_parents),
            )

        # 4. Domain verification (filters claims, produces authority verdicts)
        all_claims = list(claims)
        claims_for_general, authority_verdicts = await self._apply_domain_verification(
            verifiable_claims, step, context
        )

        # Event: domain verification routing completed
        self._emit(
            context,
            DomainVerificationCompleted(
                step_name=step.id,
                authority_verdicts=authority_verdicts,
                claims_routed_to_general=[
                    c.get("statement_id", "") for c in claims_for_general
                ],
            ),
        )

        # 5. General verification (on filtered claims only)
        verdicts, verdicts_by_model, model_timings = await verify_claims(
            handler=self,
            candidates=claims_for_general,
            question=question,
            verify_model_ids=verify_model_ids,
            step=step,
            context=context,
            prompt_ref=prompt_ref_verify,
            exec_configs=exec_configs,
            prompt_ref_verify_batch=prompt_ref_verify_batch,
            sequential_dispatch=bool(answer_pool),
        )

        # Event: per-model verdicts
        for mid, model_verdicts in verdicts_by_model.items():
            self._emit(
                context,
                ModelVerdictCast(
                    step_name=step.id,
                    model_id=mid,
                    verdicts=model_verdicts,
                ),
            )

        # 5b. Tiebreaker: route borderline claims to a stronger (slower) model
        tiebreaker_alias = step.get_domain_field("tiebreaker_model")
        if tiebreaker_alias is None:
            tiebreaker_alias = (context.options or {}).get("tiebreaker_model")

        tiebreaker_stats: dict[str, Any] = {}
        if tiebreaker_alias and claims_for_general:
            tiebreaker_alias = str(tiebreaker_alias)
            skip_reason = self._should_skip_tiebreaker(
                tiebreaker_alias,
                verify_model_aliases,
                originator,
                step,
            )
            if skip_reason:
                tiebreaker_stats = {"skipped": True, "reason": skip_reason}
            else:
                tiebreaker_stats = await self._run_tiebreaker(
                    tiebreaker_alias=tiebreaker_alias,
                    claims=claims_for_general,
                    question=question,
                    verdicts=verdicts,
                    verdicts_by_model=verdicts_by_model,
                    model_timings=model_timings,
                    verification_policy=verification_policy,
                    verification_chunk_size=verification_chunk_size,
                    step=step,
                    context=context,
                    registry=registry,
                    prompt_ref_verify=prompt_ref_verify,
                    prompt_ref_verify_batch=prompt_ref_verify_batch,
                )

        # Remap verdicts_by_model keys: resolved model ID → alias
        # (UI and verifier_pool use aliases; verify_claims uses resolved IDs)
        _id_to_alias: dict[str, str] = dict(
            zip(verify_model_ids, verify_model_aliases, strict=True)
        )
        if tiebreaker_alias:
            _tb_id = self._resolve_model_alias(tiebreaker_alias, context)
            _id_to_alias[_tb_id] = tiebreaker_alias
        verdicts_by_model = {
            _id_to_alias.get(mid, mid): v for mid, v in verdicts_by_model.items()
        }

        verification_timing = {
            "total_models": len(model_timings),
            "total_latency_ms": round(sum(t.latency_ms for t in model_timings), 2),
            "per_model": [
                {
                    "model_id": t.model_id,
                    "num_claims": t.num_claims,
                    "latency_ms": round(t.latency_ms, 2),
                    "mode": t.mode,
                    "chunk_size": t.chunk_size,
                    "chunks": [
                        {
                            "chunk_index": c.chunk_index,
                            "num_items": c.num_items,
                            "latency_ms": round(c.latency_ms, 2),
                            "prompt_tokens": c.prompt_tokens,
                            "completion_tokens": c.completion_tokens,
                        }
                        for c in t.chunks
                    ],
                    "prompt_tokens": t.prompt_tokens,
                    "completion_tokens": t.completion_tokens,
                }
                for t in model_timings
            ],
            "tiebreaker": tiebreaker_stats,
        }

        # 6. Filter general claims by threshold
        general_accepted = filter_claims(
            claims_for_general,
            verdicts,
            question_type,
            verification_policy=verification_policy,
            math_verification_policy=math_verification_policy,
        )

        # Event: threshold applied — accepted vs rejected from general verification
        general_accepted_ids = [c.get("statement_id", "") for c in general_accepted]
        general_rejected_ids = [
            c.get("statement_id", "")
            for c in claims_for_general
            if c.get("statement_id")
            not in {a.get("statement_id") for a in general_accepted}
        ]
        self._emit(
            context,
            ThresholdApplied(
                step_name=step.id,
                accepted_ids=general_accepted_ids,
                rejected_ids=general_rejected_ids,
                policy=verification_policy,
                math_policy=math_verification_policy,
            ),
        )

        # 6b. Derive compound parent verdicts: parent passes iff ALL subclaims pass
        if compound_parents:
            _accepted_ids = {c.get("statement_id") for c in general_accepted}
            for sid, av in authority_verdicts.items():
                if av.get("verdict") and av.get("final"):
                    _accepted_ids.add(sid)

            _parent_to_subs: dict[str, list[str]] = {}
            for c in all_claims:
                pid = c.get("parent_statement_id")
                sid_c = c.get("statement_id")
                if pid and sid_c:
                    _parent_to_subs.setdefault(pid, []).append(sid_c)

            for parent in compound_parents:
                pid = parent.get("statement_id", "")
                sub_ids = _parent_to_subs.get(pid, [])
                parent_pass = bool(sub_ids) and all(
                    sid in _accepted_ids for sid in sub_ids
                )
                if parent_pass:
                    general_accepted.append(parent)

        # 7. Merge authority verdicts with general results
        accepted, rejected = merge_authority_verdicts(
            all_claims, general_accepted, authority_verdicts
        )

        latency_ms = (time.time() - start_time) * 1000
        model_summary = ", ".join(
            f"{t.model_id}={t.latency_ms:.0f}ms ({t.num_claims} claims, {t.mode})"
            for t in model_timings
        )
        logger.info(
            "Step '%s': verify chain complete — %d accepted, %d rejected (%.0fms) "
            "[decompose=%.0fms, classify=%.0fms, verify: %s]",
            step.id,
            len(accepted),
            len(rejected),
            latency_ms,
            decompose_latency_ms,
            classify_latency_ms,
            model_summary,
        )

        # Event: verification complete with full results
        self._emit(
            context,
            VerificationComplete(
                step_name=step.id,
                verified_facts=accepted,
                rejected_claims=rejected,
                verdicts_by_model=verdicts_by_model,
                verifier_pool=verifier_pool_aliases,
                originator=originator or "",
                stats={
                    "total_claims": len(all_claims),
                    "accepted": len(accepted),
                    "rejected": len(rejected),
                    "latency_ms": latency_ms,
                },
                answer_sentences=answer_sentences,
            ),
        )

        return StepOutput(
            raw="",
            json={
                "verified_facts": accepted,
                "rejected_claims": rejected,
                "verdicts_by_model": verdicts_by_model,
                "authority_verdicts": authority_verdicts,
                "verifier_pool": verifier_pool_aliases,
                "originator": originator or "",
                "stats": {
                    "total_claims": len(all_claims),
                    "accepted": len(accepted),
                    "rejected": len(rejected),
                    "latency_ms": latency_ms,
                    "decompose_latency_ms": round(decompose_latency_ms, 2),
                    "contextualize_latency_ms": round(contextualize_latency_ms, 2),
                    "classify_latency_ms": round(classify_latency_ms, 2),
                    "verification_timing": verification_timing,
                },
            },
            step_id=step.id,
            latency_ms=latency_ms,
        )

    async def _apply_domain_verification(
        self,
        claims: list[dict[str, Any]],
        step: StepConfig,
        context: PipelineContext,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Delegate to _domain_verification module."""
        return await apply_domain_verification(self, claims, step, context)

    def _should_skip_tiebreaker(
        self,
        tiebreaker_alias: str,
        pool_aliases: list[str],
        originator: str | None,
        step: StepConfig,
    ) -> str | None:
        """Return skip reason if tiebreaker should not run, else None."""
        if tiebreaker_alias in pool_aliases:
            logger.info(
                "Step '%s': tiebreaker '%s' already in verifier pool, skipping",
                step.id,
                tiebreaker_alias,
            )
            return "already_in_pool"
        exclude_self = step.get_domain_field("exclude_self", False)
        if exclude_self and originator == tiebreaker_alias:
            logger.info(
                "Step '%s': tiebreaker '%s' is originator, skipping",
                step.id,
                tiebreaker_alias,
            )
            return "is_originator"
        return None

    async def _run_tiebreaker(
        self,
        *,
        tiebreaker_alias: str,
        claims: list[dict[str, Any]],
        question: str,
        verdicts: dict[str, list[bool]],
        verdicts_by_model: dict[str, dict[str, VerdictEntry]],
        model_timings: list[Any],
        verification_policy: str,
        verification_chunk_size: int | None,
        step: StepConfig,
        context: PipelineContext,
        registry: Any,
        prompt_ref_verify: str,
        prompt_ref_verify_batch: str | None,
    ) -> dict[str, Any]:
        """Run tiebreaker model on borderline claims, merge verdicts in-place.

        Excludes math domain claims from tiebreaker (they use unanimous_reject
        policy with math authority, not general consensus tiebreaking).

        Returns stats dict for inclusion in verification_timing.
        """
        # Filter out math claims - they should not go to tiebreaker
        non_math_claims = [c for c in claims if c.get("domain") != "math"]
        math_count = len(claims) - len(non_math_claims)
        if math_count:
            logger.info(
                "Step '%s': excluded %d math claims from tiebreaker consideration",
                step.id,
                math_count,
            )

        if not non_math_claims:
            logger.info(
                "Step '%s': no non-math claims for tiebreaker, skipped", step.id
            )
            return {"skipped": True, "reason": "no_non_math_claims"}

        borderline = find_borderline_claims(
            non_math_claims, verdicts, verification_policy
        )
        if not borderline:
            logger.info("Step '%s': no borderline claims, tiebreaker skipped", step.id)
            return {"skipped": True, "reason": "no_borderline_claims"}

        # Event: tiebreaker triggered with borderline claims
        self._emit(
            context,
            TiebreakerTriggered(
                step_name=step.id,
                borderline_claim_ids=[c.get("statement_id", "") for c in borderline],
                tiebreaker_model=tiebreaker_alias,
                total_claims=len(non_math_claims),
                math_excluded=math_count,
            ),
        )

        tiebreaker_model_id = self._resolve_model_alias(tiebreaker_alias, context)
        tb_model_config = registry.get_model_config(
            tiebreaker_alias,
            domain=context.pipeline.domain,
        )
        tb_exec_config = get_execution_config(tb_model_config)
        if verification_chunk_size is not None:
            tb_exec_config.chunk_size = min(
                int(verification_chunk_size),
                tb_exec_config.chunk_size,
            )

        tb_verdicts, tb_by_model, tb_timings = await verify_claims(
            handler=self,
            candidates=borderline,
            question=question,
            verify_model_ids=[tiebreaker_model_id],
            step=step,
            context=context,
            prompt_ref=prompt_ref_verify,
            exec_configs={tiebreaker_model_id: tb_exec_config},
            prompt_ref_verify_batch=prompt_ref_verify_batch,
        )

        # Merge tiebreaker verdicts into main results (mutates caller's dicts)
        for sid, tb_votes in tb_verdicts.items():
            verdicts.setdefault(sid, []).extend(tb_votes)
        verdicts_by_model.update(tb_by_model)
        model_timings.extend(tb_timings)

        tb_latency = sum(t.latency_ms for t in tb_timings)
        logger.info(
            "Step '%s': tiebreaker '%s' verified %d/%d borderline claims (%.0fms)",
            step.id,
            tiebreaker_alias,
            len(borderline),
            len(non_math_claims),
            tb_latency,
        )
        return {
            "model": tiebreaker_alias,
            "borderline_claims": len(borderline),
            "total_claims": len(non_math_claims),
            "math_claims_excluded": len(claims) - len(non_math_claims),
            "latency_ms": round(tb_latency, 2),
        }

    def _resolve_verify_models(
        self,
        step: StepConfig,
        context: PipelineContext,
        originator: str | None = None,
    ) -> list[str]:
        """
        Resolve verifier model aliases from step config or pipeline options.

        Supports both legacy explicit lists and model_pool + exclude_self pattern.
        When model_pool and exclude_self are configured, automatically excludes
        the originator model from the verification pool.
        """
        # Check for model_pool + exclude_self pattern
        model_pool = step.get_domain_field("model_pool")
        exclude_self = step.get_domain_field("exclude_self", False)

        if model_pool:
            pool = []
            if isinstance(model_pool, list):
                pool = model_pool
            elif isinstance(model_pool, str):
                # Resolve from options namespace
                options = context.options or {}
                pool_from_options = options.get(model_pool.split(".")[-1], [])
                if isinstance(pool_from_options, list):
                    pool = pool_from_options

            # Apply exclude_self logic
            if exclude_self and originator and pool:
                pool = [m for m in pool if m != originator]
                logger.info(
                    "Step '%s': exclude_self=true, excluded originator '%s' from pool",
                    step.id,
                    originator,
                )

            return pool

        # Legacy: explicit verify_models list
        verify_models = step.get_domain_field("verify_models")
        if verify_models and isinstance(verify_models, list):
            return verify_models

        # Fallback: verify_models from options
        options = context.options or {}
        vm = options.get("verify_models", {})
        if isinstance(vm, dict):
            return list(vm.values())
        if isinstance(vm, list):
            return vm
        return []

    def _require_domain_field(self, step: StepConfig, key: str) -> str:
        """Get required string config from step domain fields."""
        value = step.get_domain_field(key, "")
        if not value:
            logger.error("Step '%s': missing '%s' in step config", step.id, key)
            raise ValueError(f"Step '{step.id}' missing '{key}' in step config")
        return str(value)

    @override
    def validate(self, step: StepConfig) -> list[str]:
        """Validate step configuration."""
        errors: list[str] = []
        if not step.model_ref:
            errors.append(f"Step '{step.id}' missing model_ref")
        if not step.handler_inputs or "answer" not in step.handler_inputs:
            errors.append(f"Step '{step.id}' missing 'answer' in handler_inputs")
        if not step.handler_inputs or "question" not in step.handler_inputs:
            errors.append(f"Step '{step.id}' missing 'question' in handler_inputs")
        return errors
