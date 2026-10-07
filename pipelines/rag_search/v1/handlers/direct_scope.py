"""Direct scope handler — returns fixed scope without LLM calls.

Used by the rag-context-v1-direct pipeline variant to skip the entire
rewriting chain. Provides the scope_result that retrieve_assemble requires,
using either a pipeline option (scope_override) or defaulting to "all".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ..unscoped_intent_scopes import expand_default_scopes_for_query

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class DirectScopeHandler(BaseHandler):
    """Return a fixed scope result with no LLM call."""

    step_type = "rag_direct_scope_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        effective = context.options
        scope_override = effective.get("scope_override", "")

        if isinstance(scope_override, list) and scope_override:
            scopes = [str(s) for s in scope_override if str(s).strip()]
        elif isinstance(scope_override, str) and scope_override.strip():
            scopes = [scope_override.strip()]
        else:
            default_scope: str = step.get_domain_field("default_scope", "all")
            scopes = [default_scope]

        expanded = False
        query_text = str(context.source_text or "").strip()
        if query_text and not (
            isinstance(scope_override, list) and scope_override
        ) and not (isinstance(scope_override, str) and scope_override.strip()):
            scopes, expanded = expand_default_scopes_for_query(query_text, scopes)

        result: dict[str, object] = {
            "needs_retrieval": True,
            "scopes": scopes,
            "scope_confidence": 1.0,
            "out_of_scope_reason": "",
            "must_include": [],
        }
        if expanded:
            result["scope_source"] = "classifier"

        logger.info(
            "Step '%s': direct scope — scopes=%s (no LLM analysis%s)",
            step.id,
            scopes,
            ", intent-expanded" if expanded else "",
        )

        return StepOutput(
            raw=str(result),
            json=result,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        return []
