"""Admission, context injection, and prompt-block construction for frontier_dispatch_v1.

Package-private admission helpers for ``frontier_dispatch_v1``. Eight responsibilities:

1. ``check_agent_model_consistency`` — pre-hydration guard for concrete
   family/platform seats. Functional roles are model-agnostic; explicit model
   overrides may fill any role.

2. ``check_boot_provider_compatibility`` — pre-hydration telemetry for models
   with ``mcp_client_tool_loop=False`` when ``mcp_enabled=True``: those models
   reject client-side MCP function tools; server-side built-ins are governed
   separately by the ``server_tools`` knob.

3. ``warn_caller_mcp_disabled`` — telemetry when the caller explicitly passes
   ``mcp=False`` on an MCP-capable model (full catalog suppressed).

4. ``prepend_dispatch_context`` — injects a minimal ``<dispatch_context>``
   preamble into every system prompt, anchoring temporal reasoning with
   today's UTC date.

5. ``reject_unknown_runtime_options`` — validates ``context.runtime_options``
   against the handler's accepted key set; raises ``UnknownPipelineOptionsError``
   for any unknown key.

6. ``resolve_remote_mcp`` — card-derived internal remote-connector selection
   from the single caller ``mcp`` boolean.

7. ``validate_frontier_dispatch_step`` — config-time validation that a step's
   ``type`` is ``frontier_dispatch_v1``; returns a list of error strings for
   the step-config validator.

8. ``build_runtime_context_block`` — renders the Active Runtime Context
   markdown block that the handler optionally appends to ``system`` when
   ``step.inject_runtime_context`` is set.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agent_seat.profiles import load_roles
from agent_seat.registry import (
    check_agent_model_requirement,
    normalize_agent_slug,
    resolve_agent_model_requirement,
    resolve_agent_provider,
    resolve_agent_valid_family,
)
from model_capabilities import mcp_client_tool_loop, mcp_remote_connector
from universal_logging import get_logger

from ...events.dispatch import (
    PipelineFrontierDispatchAgentModelMismatch,
    PipelineFrontierDispatchToolSuppressed,
)
from ...execution.errors import (
    AgentModelMismatchError,
    UnknownPipelineOptionsError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...schemas import StepConfig
    from ..protocol import PipelineContext

logger = get_logger(__name__)


def prepend_dispatch_context(system: str) -> str:
    """Prepend ``<dispatch_context>`` preamble with the current UTC date.

    v1: ``current_date`` only — always injected, no opt-out. Anchors
    temporal reasoning for every ``frontier_dispatch_v1`` execution regardless
    of persona/raw dispatch tier.
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
    model_entity_id: str,
    provider: str,
    execution_id: str,
    publish: Callable[[object], None],
) -> None:
    """Reject concrete-seat/model conflicts; never provider-lock roles.

    Functional roles (``reviewer``, ``skeptic``, etc.) are model-agnostic:
    any model can assume any role when explicitly requested. The default
    role assignment is only a convention for omitted ``model``. Concrete
    family/platform seats (for example ``grok-api-multi``) still enforce
    their provider and variant requirements.

    ``model_entity_id`` is included on the published Mismatch event so post-hoc
    correlators can recover the canonical Cortex ``model:<slug>`` directly —
    Mismatch fires during admission and ``.started`` is never emitted on the
    rejection path, leaving ``execution_id`` without an outcome event to join
    against. Mirrors the recovery shape used on
    ``pipeline.frontier.dispatch.remotemcp.misconfigured``.
    """
    if normalize_agent_slug(agent) in load_roles():
        return

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
                model_entity_id=model_entity_id,
                valid_family=valid_family,
                mismatch_kind="provider",
            ),
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
                model_entity_id=model_entity_id,
                valid_family=valid_family,
                mismatch_kind="variant",
            ),
        )
        raise AgentModelMismatchError(
            agent=agent,
            model=model,
            provider=provider,
            expected_provider=expected,
            required_variant=resolve_agent_model_requirement(agent),
        )


def check_boot_provider_compatibility(
    *,
    agent: str | None,
    model: str,
    provider: str,
    mcp_enabled: bool,
    execution_id: str,
    publish: Callable[[object], None],
) -> None:
    """Emit MCP-class coercion telemetry for no-client-loop models; no raise.

    When ``mcp_enabled=True`` and the model card carries
    ``mcp_client_tool_loop=False``, silently allow dispatch with ``tools=[]`` /
    ``mcp_tool_loop=False`` and emit ``pipeline.frontier.dispatch.tool.suppressed``
    with reason ``mcp_client_tool_loop_unsupported``. Server-side built-ins are
    governed separately by the ``server_tools`` knob.

    Skipped when ``agent is None`` (persona-free) or ``not mcp_enabled``.
    """
    if agent is None or not mcp_enabled:
        return
    if mcp_client_tool_loop(model):
        return
    publish(
        PipelineFrontierDispatchToolSuppressed(
            execution_id=execution_id,
            agent=agent,
            model=model,
            provider=provider,
            reason="mcp_client_tool_loop_unsupported",
        ),
    )


def warn_caller_mcp_disabled(
    *,
    opts: dict[str, Any],
    model: str,
    agent: str | None,
    provider: str,
    execution_id: str,
    publish: Callable[[object], None],
) -> None:
    """Warn when caller explicitly opts out of MCP on an MCP-capable model."""
    if "mcp" not in opts or opts.get("mcp"):
        return
    if not mcp_client_tool_loop(model):
        return
    logger.warning(
        "dispatch mcp=False suppresses MCP tool catalog for MCP-capable model %s",
        model,
    )
    publish(
        PipelineFrontierDispatchToolSuppressed(
            execution_id=execution_id,
            agent=agent,
            model=model,
            provider=provider,
            reason="caller_mcp_false",
        ),
    )


# Keys injected into ``runtime_options`` by the framework itself — never
# caller-meaningful pipeline_options. ``stream`` is surfaced from the outer
# request body (coerced at proxy ingress by ``_coerce_stream_flag`` and folded
# into runtime_options by ``extract_runtime_options``) for the generate
# handler's stream-passthrough branch. Frontier dispatch is non-streaming and
# does not accept it as a caller option, so it must be excluded from the
# unknown-key computation rather than rejected.
_FRAMEWORK_INJECTED_RUNTIME_OPTION_KEYS: frozenset[str] = frozenset({"stream"})


def reject_unknown_runtime_options(
    step: StepConfig,
    context: PipelineContext,
    accepted_keys: frozenset[str],
) -> None:
    """Hard-reject ``context.runtime_options`` keys outside ``accepted_keys``.

    Validates only caller-supplied HTTP keys, not YAML defaults folded in by
    the framework (those appear in ``context.options`` but not in
    ``context.runtime_options``) and not framework-injected normalization keys
    such as ``stream`` (see ``_FRAMEWORK_INJECTED_RUNTIME_OPTION_KEYS``).
    """
    runtime: dict[str, Any] = getattr(context, "runtime_options", None) or {}
    if not runtime:
        return
    candidate_keys = set(runtime.keys()) - _FRAMEWORK_INJECTED_RUNTIME_OPTION_KEYS
    unknown = sorted(candidate_keys - accepted_keys)
    if not unknown:
        return
    role_raw = runtime.get("role")
    role = str(role_raw).strip() if isinstance(role_raw, str) else None
    raise UnknownPipelineOptionsError(
        step_name=step.id,
        unknown_keys=unknown,
        accepted_keys=sorted(accepted_keys),
        agent=role or None,
    )


def resolve_remote_mcp(*, model: str, mcp_enabled: bool) -> bool:
    """Return whether the card-selected remote-connector path is active.

    Internal selection only — callers supply the single ``mcp`` boolean;
    remote-vs-client-loop is derived from ``mcp_remote_connector(model)``.
    Missing card/field propagates ``CapabilityCardError`` to the existing
    structured 422 translation — never a fallback.
    """
    return mcp_enabled and mcp_remote_connector(model)


def validate_frontier_dispatch_step(step: StepConfig) -> list[str]:
    """Validate a frontier_dispatch_v1 step configuration."""
    errors: list[str] = []
    if step.type != "frontier_dispatch_v1":
        errors.append(f"Step '{step.id}': expected type frontier_dispatch_v1")
    return errors


def build_runtime_context_block(
    *,
    pipeline_id: str,
    model: str,
    reasoning_effort: str,
    boot_profile: str,
    max_turns: int,
) -> str:
    """Render the Active Runtime Context markdown block for system-prompt injection.

    Called when ``step.inject_runtime_context`` is set. The block names the
    pipeline, the resolved model, the effective reasoning effort, the boot
    profile, and the tool-loop budget — ground truth for *this* turn that
    overrides any "default model" the persona briefing may name.
    """
    return (
        "\n\n## Active Runtime Context\n\n"
        f"- pipeline_id: {pipeline_id}\n"
        f"- model: {model} (resolved at dispatch time)\n"
        f"- reasoning_effort: {reasoning_effort}\n"
        f"- boot_profile: {boot_profile}\n"
        f"- tool_loop_budget: {max_turns} turns\n"
        "\n"
        "This block is injected by the dispatch handler and reflects "
        "ground truth for *this* turn. Your persona briefing may name "
        'a different "default model" — when in doubt, this block is '
        "authoritative for the current call. The operator may switch "
        "you to a different tier (mini / high / team-leader) between "
        "turns; expect this block to change accordingly.\n"
    )
