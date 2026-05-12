"""Admission checks and context injection for frontier_dispatch_v1.

Kept in a sibling module to hold frontier_dispatch.py under the file-size
ceiling.  Five responsibilities:

1. ``check_agent_model_consistency`` — pre-hydration guard for concrete
   family/platform seats. Functional roles are model-agnostic; explicit model
   overrides may fill any role.

2. ``check_boot_provider_compatibility`` — pre-hydration guard that rejects
   xAI multi-agent model dispatches with ``boot_mode='team'`` and
   ``mcp_enabled=True``: those models reject client-side MCP function tools.
   Non-multi-agent xAI models (grok-4.3, grok-4.20-reasoning) are not gated.

3. ``prepend_dispatch_context`` — injects a minimal ``<dispatch_context>``
   preamble into every system prompt, anchoring temporal reasoning with
   today's UTC date.

4. ``reject_unknown_runtime_options`` — validates ``context.runtime_options``
   against the handler's accepted key set; raises ``UnknownPipelineOptionsError``
   for any unknown key.

5. ``resolve_remote_mcp`` — validates and resolves the ``remote_mcp`` option
   against provider support and the ``mcp`` gate; raises
   ``RemoteMcpUnsupportedError`` on violation.
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

from ..events.dispatch import (
    PipelineFrontierDispatchAgentModelMismatch,
    PipelineFrontierDispatchRemoteMcpUnsupported,
    PipelineFrontierDispatchToolSuppressed,
)
from ..execution.errors import (
    AgentModelMismatchError,
    RemoteMcpUnsupportedError,
    UnknownPipelineOptionsError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..schemas import StepConfig
    from .protocol import PipelineContext


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


# xAI multi-agent models (identified by "multi-agent" in the model ID) reject
# client-side MCP function tools at the API level. Non-multi-agent xAI models
# (e.g. grok-4.3, grok-4.20-reasoning variants) support standard function
# calling and must NOT be gated. The "multi-agent" substring is the canonical
# signal — it matches model_requirement="multi-agent" on the grok-api-multi
# concrete seat profile.
_XAI_MULTI_AGENT_SUBSTRING: str = "multi-agent"


def check_boot_provider_compatibility(
    *,
    agent: str | None,
    model: str,
    provider: str,
    mcp_enabled: bool,
    opt_tools: Any,
    execution_id: str,
    publish: Callable[[object], None],
) -> None:
    """Apply silent provider-derived tool coercion for incompatible (provider, boot)
    pairs; no longer raises to caller.

    Per todo:retire-tools-allowlist-as-caller-concern, tools is not a caller
    concern. For xAI multi-agent models (contains "multi-agent") with
    mcp_enabled=True, silently coerce by allowing dispatch with tools=[] /
    mcp_tool_loop=False (no client-side MCP function tools) and emit
    ``pipeline.frontier.dispatch.tool.suppressed`` telemetry. Server-side
    xAI builtins still injected via provider_options.xai.tools.

    Non-multi-agent xAI models and other providers are unaffected.

    Skipped (no coercion) when:
    - ``isinstance(opt_tools, list)`` — explicit caller intent via frontier_dispatch
    - ``agent is None`` — persona-free dispatch
    - ``not mcp_enabled`` — caller already suppressed
    - not xAI multi-agent model

    The BootProviderMismatchError remains for genuine unresolvable misconfigs.
    """
    if isinstance(opt_tools, list) or agent is None or not mcp_enabled:
        return
    if provider != "xai" or _XAI_MULTI_AGENT_SUBSTRING not in model:
        return
    reason = "xai_multi_agent_client_tools_unsupported"
    publish(
        PipelineFrontierDispatchToolSuppressed(
            execution_id=execution_id,
            agent=agent,
            model=model,
            provider=provider,
            reason=reason,
        ),
    )
    # Silent coercion: no error raised to caller. Downstream
    # resolve_dispatch_tool_set sets tools=[] for this case; mcp_tool_loop=False;
    # CORTEX_TOOL_QUICKREF suppressed in prompt. Provider builtins via
    # provider_options.xai.tools still available.


def reject_unknown_runtime_options(
    step: StepConfig,
    context: PipelineContext,
    accepted_keys: frozenset[str],
) -> None:
    """Hard-reject ``context.runtime_options`` keys outside ``accepted_keys``.

    Validates only caller-supplied HTTP keys, not YAML defaults folded in by
    the framework (those appear in ``context.options`` but not in
    ``context.runtime_options``).
    """
    runtime: dict[str, Any] = getattr(context, "runtime_options", None) or {}
    if not runtime:
        return
    unknown = sorted(set(runtime.keys()) - accepted_keys)
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


_REMOTE_MCP_PROVIDERS: frozenset[str] = frozenset({"anthropic"})


def resolve_remote_mcp(
    *,
    opts: dict[str, Any],
    step: StepConfig,
    context: PipelineContext,
    provider: str,
    model: str,
    agent: str | None,
    mcp_enabled: bool,
    publish: Callable[[object], None],
) -> bool:
    """Resolve and validate ``remote_mcp`` against provider support and mcp gate.

    Default: ``True`` iff ``provider=anthropic`` and ``mcp_enabled`` — otherwise
    ``False``. Explicit ``remote_mcp=True`` is rejected when either (a)
    ``mcp_enabled=False`` (remote_mcp requires mcp) or (b) the provider is not
    in ``_REMOTE_MCP_PROVIDERS`` (anthropic-only). Violations emit
    ``pipeline.frontier.dispatch.remotemcp.unsupported`` and raise
    ``RemoteMcpUnsupportedError`` before hydration.
    """
    supports = provider in _REMOTE_MCP_PROVIDERS
    raw = opts.get("remote_mcp")
    if raw is None:
        return supports and mcp_enabled
    requested = bool(raw)
    if not requested:
        return False
    reason: str | None = None
    if not mcp_enabled:
        reason = (
            "remote_mcp=True requires mcp=True — remote MCP is only "
            "meaningful when client-side MCP tooling is enabled"
        )
    elif not supports:
        reason = (
            f"remote_mcp=True is only supported for anthropic models; "
            f"provider={provider!r} has no native mcp_toolset path"
        )
    if reason is not None:
        publish(
            PipelineFrontierDispatchRemoteMcpUnsupported(
                execution_id=context.execution_id,
                agent=agent,
                model=model,
                provider=provider,
                requested=requested,
                reason=reason,
            ),
        )
        raise RemoteMcpUnsupportedError(
            step_name=step.id,
            provider=provider,
            model=model,
            agent=agent,
            requested=requested,
            reason=reason,
        )
    return True
