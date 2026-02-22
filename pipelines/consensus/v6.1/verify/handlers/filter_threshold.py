"""Final verification gate: decide which claims survive consensus.

Collects per-model verdicts from the general verification step and
authority verdicts from domain-specific verifiers (e.g. math), then:

1. **Threshold filtering** — each general claim is accepted or rejected
   by applying a configurable voting policy (majority, unanimous, etc.)
   to the per-model boolean verdicts.  Cascade rejection removes claims
   whose logical antecedents were rejected.
2. **Compound parent resolution** — a compound (decomposed) claim is
   accepted only when *all* of its sub-claims passed.
3. **Authority merge** — domain verifier results take precedence:
   authority-rejected claims are always rejected, authority-accepted
   (final) claims bypass general voting, and non-final authority claims
   fall through to the general result.

Produces the terminal verified_facts / rejected_claims partition that
the synthesize stage consumes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ...shared._chain_utils import filter_claims
from ...shared._domain_verification import merge_authority_verdicts

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class FilterThresholdHandler(BaseHandler):
    """Partition all claims into verified_facts and rejected_claims.

    Applies the configured verification_policy to general verdicts,
    resolves compound parents via sub-claim conjunction, and merges
    authority (domain-verifier) outcomes into the final partition.
    """

    step_type: str = "consensus_filter_threshold_v6_1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        resolver = NamespaceResolver(context)
        claims_for_general: list[dict[str, Any]] = self._resolve_input(
            resolver, step, "claims_for_general", step.handler_inputs
        )
        verdicts: dict[str, list[bool]] = self._resolve_input(
            resolver, step, "verdicts", step.handler_inputs
        )
        authority_verdicts: dict[str, dict[str, Any]] = self._resolve_input(
            resolver, step, "authority_verdicts", step.handler_inputs
        )
        all_claims: list[dict[str, Any]] = self._resolve_input(
            resolver, step, "all_claims", step.handler_inputs
        )
        compound_parents: list[dict[str, Any]] = self._resolve_input(
            resolver, step, "compound_parents", step.handler_inputs
        )
        question_type = str(
            self._resolve_input(resolver, step, "question_type", step.handler_inputs)
            or "general"
        )
        verdicts_by_model: dict[str, Any] = self._resolve_input(
            resolver, step, "verdicts_by_model", step.handler_inputs
        )

        verification_policy = str(
            step.get_domain_field("verification_policy") or "majority"
        )
        math_verification_policy = str(
            step.get_domain_field("math_verification_policy") or "unanimous_reject"
        )

        general_accepted = filter_claims(
            claims_for_general,
            verdicts,
            question_type,
            verification_policy=verification_policy,
            math_verification_policy=math_verification_policy,
        )

        # Derive compound parent verdicts: parent passes iff ALL sub-claims pass
        if compound_parents:
            accepted_ids = {c.get("statement_id") for c in general_accepted}
            for sid, av in authority_verdicts.items():
                if av.get("verdict") and av.get("final"):
                    accepted_ids.add(sid)

            parent_to_subs: dict[str, list[str]] = {}
            for c in all_claims:
                pid = c.get("parent_statement_id")
                sid_c = c.get("statement_id")
                if pid and sid_c:
                    parent_to_subs.setdefault(pid, []).append(sid_c)

            for parent in compound_parents:
                pid = parent.get("statement_id", "")
                sub_ids = parent_to_subs.get(pid, [])
                if sub_ids and all(sid in accepted_ids for sid in sub_ids):
                    general_accepted.append(parent)

        accepted, rejected = merge_authority_verdicts(
            all_claims, general_accepted, authority_verdicts
        )

        return StepOutput(
            raw="",
            json={
                "verified_facts": accepted,
                "rejected_claims": rejected,
                "authority_verdicts": authority_verdicts,
                "verdicts_by_model": verdicts_by_model,
            },
        )
