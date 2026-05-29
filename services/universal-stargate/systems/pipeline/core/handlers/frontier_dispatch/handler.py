"""``FrontierDispatchHandler`` — native-endpoint frontier dispatch step handler.

Thin class shell for the ``frontier_dispatch_v1`` step type: owns the registry
decorator, step type, the curated team-tool tier, and the accepted-runtime-
option allowlist, plus a delegating ``execute`` that threads the four phase
functions (admission → gen-params → native-loop → completion). ``validate``
delegates to the admission sibling. All phase logic lives in the package's
free-function submodules per the class-delegator pattern shared with
``generate/`` and ``assess_loop/``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..builtin import BaseHandler
from ..frontier_dispatch_admission import validate_frontier_dispatch_step
from ..protocol import StepOutput
from ..registry import register_handler
from .admission_gate import run_admission_gate
from .completion import build_dispatch_output
from .gen_params import build_frontier_request
from .native_loop import run_dispatch_loop

if TYPE_CHECKING:
    from ..protocol import PipelineContext
    from ..schemas import StepConfig


@register_handler
class FrontierDispatchHandler(BaseHandler):
    """Native-endpoint frontier dispatch with persona-conditional hydration."""

    step_type: str = "frontier_dispatch_v1"
    # ``_TEAM_TOOL_NAMES`` is the curated tier consulted only by Anthropic
    # persona-bound dispatch (Case 2, anthropic branch in
    # ``resolve_dispatch_tool_set``). Other providers — and persona-free
    # dispatch (Case 3) — receive the full live MCP catalog. The previous
    # ``_READ_TOOL_NAMES = ("cortex", "rag")`` curated read-only tier was
    # retired with the BOE-19-P case-study reopening (Cortex assertion 7974,
    # 2026-05-01): tool surface is no longer dispatch-path-dependent.
    _TEAM_TOOL_NAMES: tuple[str, ...] = ("cortex", "rag", "agent_bus")

    # Caller-supplied keys accepted on ``pipeline_options`` for
    # ``frontier_dispatch_v1``. Anything outside this set is rejected at
    # admission with ``UnknownPipelineOptionsError`` — silent drops have
    # cost real debugging time (e.g. top-level ``effort`` ignored when the
    # canonical key is ``generation_parameters.reasoning_effort``).
    #
    # ``_endpoint_request_id`` marks canonical endpoint arrivals. The proxy
    # router uses it to suppress the raw-pipeline persona-bypass hint, and the
    # handler uses it to admit endpoint-supplied tool overrides with an agent.
    _ACCEPTED_RUNTIME_OPTION_KEYS: frozenset[str] = frozenset(
        {
            "model",
            "role",
            "mcp",
            "remote_mcp",
            "tools",
            "max_tool_turns",
            "system",
            "generation_parameters",
            "model_entity_id",
            "_endpoint_request_id",
            # dispatch-surface-split Phase 1: consumed by output_short gate (Phase 3)
            "output_contract",
        }
    )

    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        admission = await run_admission_gate(self, step, context)
        bundle = build_frontier_request(self, step, context, admission)
        outcome = await run_dispatch_loop(self, step, context, admission, bundle.req)
        return build_dispatch_output(context, step, admission, outcome, bundle.system)

    def validate(self, step: StepConfig) -> list[str]:
        return validate_frontier_dispatch_step(step)
