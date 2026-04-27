"""Admission checks and context injection for frontier_dispatch_v1.

Kept in a sibling module to hold frontier_dispatch.py under the file-size
ceiling. Two responsibilities:

1. ``check_agent_model_consistency`` — pre-hydration guard that rejects
   dispatches where the caller-supplied model's provider conflicts with the
   agent's identity-bound provider family (e.g. ``agent='orion'`` +
   ``model='anthropic/claude-sonnet-4-6'``).

2. ``prepend_dispatch_context`` — injects a minimal ``<dispatch_context>``
   preamble into every system prompt, anchoring temporal reasoning with
   today's UTC date.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from agent_seat.registry import (
    check_agent_model_requirement,
    resolve_agent_model_requirement,
    resolve_agent_provider,
    resolve_agent_valid_family,
)

from ..events.dispatch import PipelineFrontierDispatchAgentModelMismatch
from ..execution.errors import AgentModelMismatchError


def prepend_dispatch_context(system: str) -> str:
    """Prepend ``<dispatch_context>`` preamble with the current UTC date.

    v1: ``current_date`` only — always injected, no opt-out. Anchors
    temporal reasoning for every ``frontier_dispatch_v1`` execution
    regardless of boot level.
    """
    today = datetime.now(UTC).date().isoformat()
    ctx = (
        "<dispatch_context>\n"
        f"  <current_date>{today}</current_date>\n"
        "</dispatch_context>"
    )
    return f"{ctx}\n\n{system}" if system else ctx


def check_agent_model_consistency(
    *,
    agent: str,
    model: str,
    provider: str,
    execution_id: str,
    publish: Callable[[object], None],
) -> None:
    """Reject dispatches where model.provider conflicts with agent family,
    or where the model fails the agent's variant requirement.

    ∀ agent ∈ registry: model.provider MUST equal agent.expected_provider.
    ∀ agent ∈ _AGENT_MODEL_REQUIREMENTS: model MUST satisfy the variant
    constraint (e.g. oppie MUST use an xAI multi-agent model).
    Unknown agents (not in registry) are not checked. Emits
    ``pipeline.frontier.dispatch.mismatch`` and raises
    ``AgentModelMismatchError`` on either violation.
    """
    expected = resolve_agent_provider(agent)
    if expected is None:
        return
    if provider != expected:
        valid_family = resolve_agent_valid_family(agent)
        publish(
            PipelineFrontierDispatchAgentModelMismatch(
                execution_id=execution_id,
                agent=agent,
                requested_model=model,
                valid_family=valid_family,
                mismatch_kind="provider",
            )
        )
        raise AgentModelMismatchError(
            agent=agent,
            model=model,
            provider=provider,
            expected_provider=expected,
        )
    violation = check_agent_model_requirement(agent, model)
    if violation:
        valid_family = resolve_agent_valid_family(agent)
        publish(
            PipelineFrontierDispatchAgentModelMismatch(
                execution_id=execution_id,
                agent=agent,
                requested_model=model,
                valid_family=valid_family,
                mismatch_kind="variant",
            )
        )
        raise AgentModelMismatchError(
            agent=agent,
            model=model,
            provider=provider,
            expected_provider=expected,
            required_variant=resolve_agent_model_requirement(agent),
        )
