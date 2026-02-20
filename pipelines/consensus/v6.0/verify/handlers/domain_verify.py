"""Route domain-tagged claims to specialist authority models.

Partitions the claim list into two tracks:

1. **Authority track** — claims whose domain has a configured verifier
   (e.g. math claims verified by a specialist math model).  These
   receive authority verdicts: ``verdict=True, final=True`` means the
   claim is accepted without needing general-pool votes;
   ``verdict=False`` means authority-rejected (overrides general votes).
2. **General track** — all remaining claims (domain=general or no
   configured verifier).  These are passed to verify_general for
   cross-model majority voting.

Compound parent claims (``has_sub_claims=True``) are excluded from
both tracks — their verdict is derived later by filter_threshold
from their sub-claims.

Outputs:
    json.claims_for_general   — claims needing general verification
    json.authority_verdicts   — {statement_id: {verdict, domain, final, ...}}
    json.all_claims           — full claim list (for filter_threshold)
    json.compound_parents     — compound parent claims (for filter_threshold)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ...shared._domain_verification import apply_domain_verification

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class DomainVerifyHandler(BaseHandler):
    """Send domain claims to authority models, partition remainder for general voting.

    Authority verdicts can short-circuit general verification: a
    final-accepted claim skips voting, a rejected claim is immediately
    excluded.
    """

    step_type: str = "consensus_domain_verify_v6_0"

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

        # Exclude compound parents — verdict derived from sub-claims
        verifiable = [c for c in claims if not c.get("has_sub_claims")]
        compound_parents = [c for c in claims if c.get("has_sub_claims")]

        claims_for_general, authority_verdicts = await apply_domain_verification(
            handler=self,
            claims=verifiable,
            step=step,
            context=context,
        )

        return StepOutput(
            raw="",
            json={
                "claims_for_general": claims_for_general,
                "authority_verdicts": authority_verdicts,
                "all_claims": claims,
                "compound_parents": compound_parents,
            },
        )
