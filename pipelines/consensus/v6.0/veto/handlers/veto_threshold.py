"""Apply veto voting policy and finalize verified/rejected partition.

Consumes the veto vote vectors from veto_verify and applies the
configured veto policy (default: ``unanimous_reject`` — a claim is
vetoed only when *every* veto-pool model rejects it).

For each authority-accepted claim:
- If veto votes meet the rejection threshold → move from
  verified_facts to rejected_claims.
- Otherwise → keep in verified_facts (authority decision stands).

This is the final claim-level gate before synthesis.  The output
verified_facts and rejected_claims lists are the definitive partition
consumed by post_process / synthesize.

Outputs:
    json.verified_facts  — surviving claims (general + unvetoed authority)
    json.rejected_claims — general-rejected + vetoed authority claims
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ...shared._threshold import get_policy_fn

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class VetoThresholdHandler(BaseHandler):
    """Apply veto voting policy to authority-accepted claims.

    Claims that fail the veto threshold are moved from verified_facts
    to rejected_claims, overriding the authority acceptance.
    """

    step_type: str = "consensus_veto_threshold_v6_0"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        resolver = NamespaceResolver(context)
        verified_facts: list[dict[str, Any]] = self._resolve_input(
            resolver, step, "verified_facts", step.handler_inputs
        )
        rejected_claims: list[dict[str, Any]] = self._resolve_input(
            resolver, step, "rejected_claims", step.handler_inputs
        )
        veto_verdicts: dict[str, list[bool]] = self._resolve_input(
            resolver, step, "veto_verdicts", step.handler_inputs
        )
        authority_claims: list[dict[str, Any]] = self._resolve_input(
            resolver, step, "authority_claims", step.handler_inputs
        )

        verified_list = list(verified_facts) if verified_facts else []
        rejected_list = list(rejected_claims) if rejected_claims else []

        if not veto_verdicts or not authority_claims:
            return StepOutput(
                raw="",
                json={
                    "verified_facts": verified_list,
                    "rejected_claims": rejected_list,
                },
            )

        veto_policy_name = str(
            step.get_domain_field("veto_policy") or "unanimous_reject"
        )
        policy_fn = get_policy_fn(veto_policy_name)

        vetoed: list[dict[str, Any]] = []
        survived: list[dict[str, Any]] = []
        for claim in authority_claims:
            sid = claim.get("statement_id", "")
            votes = veto_verdicts.get(sid, [])
            true_count = sum(1 for v in votes if v)
            required = policy_fn(len(votes)) if votes else 1
            if true_count >= required:
                survived.append(claim)
            else:
                vetoed.append(claim)

        vetoed_ids = {c.get("statement_id", "") for c in vetoed}
        updated_verified = [
            c for c in verified_list if c.get("statement_id") not in vetoed_ids
        ]
        updated_rejected = rejected_list + vetoed

        logger.info(
            "Step '%s': veto threshold — %d checked, %d vetoed, %d survived",
            step.id,
            len(authority_claims),
            len(vetoed),
            len(survived),
        )

        return StepOutput(
            raw="",
            json={
                "verified_facts": updated_verified,
                "rejected_claims": updated_rejected,
            },
        )
