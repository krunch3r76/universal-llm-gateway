"""Route domain-specific claims to authority models."""

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
    """Route domain claims to authority models, partition for general flow."""

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
