"""Admission gate phase for ``frontier_dispatch_v1``.

Runs the ordered admission sequence that must precede any generation work for a
frontier dispatch: unknown-runtime-option rejection (contractually first),
agent/model/provider resolution, agent↔model consistency, remote-MCP
resolution, tool-loop budget, user-prompt resolution, boot/provider
compatibility, and the 3-way dispatch tool-set resolution. Emits
``PipelineFrontierDispatchRemoteMcpEnabled`` after tool-set resolution when
remote MCP is active. Returns an :class:`AdmissionResult` bundle consumed by the
gen-params, native-loop, and completion phases.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from llm_adapters import effective_provider_for_model
from model_id import ModelId, canonical_model_entity_id

from ...events.dispatch import PipelineFrontierDispatchRemoteMcpEnabled
from ..frontier_dispatch_admission import (
    check_agent_model_consistency,
    check_boot_provider_compatibility,
    reject_unknown_runtime_options,
    resolve_remote_mcp,
)
from ..frontier_dispatch_request import (
    resolve_agent,
    resolve_model,
    resolve_system_prompt,
    resolve_user_prompt,
)
from ..frontier_dispatch_tools import resolve_dispatch_tool_set

if TYPE_CHECKING:
    from ..protocol import PipelineContext
    from ..schemas import StepConfig
    from .handler import FrontierDispatchHandler


@dataclass
class AdmissionResult:
    """Resolved dispatch inputs produced by the admission gate.

    Carries every value the downstream gen-params, native-loop, and completion
    phases need so they do not re-resolve agent/model/provider or rebuild the
    publish closure. ``publish`` is the bound bus-event emitter for this
    execution; ``system`` here is the post-tool-set system prompt (before the
    dispatch-context prepend applied in the gen-params phase).
    """

    opts: dict[str, Any]
    agent: Any
    model: str
    model_entity_id: str
    provider: str
    publish: Callable[[Any], None]
    mcp_enabled: bool
    remote_mcp: Any
    max_turns: int
    user_prompt: str
    opt_tools: Any
    boot_profile: str
    tools: Any
    system: str | None
    hydration_meta: Any


async def run_admission_gate(
    handler: FrontierDispatchHandler,
    step: StepConfig,
    context: PipelineContext,
) -> AdmissionResult:
    """Execute the ordered admission sequence and resolve the dispatch tool set.

    ``reject_unknown_runtime_options`` MUST run before any read of
    ``context.options`` (contractual admission-first invariant). The
    ``RemoteMcpEnabled`` event is emitted after tool-set resolution when remote
    MCP is active, matching the monolith event order (before any generation
    params are built or the Started event fires).
    """
    reject_unknown_runtime_options(step, context, handler._ACCEPTED_RUNTIME_OPTION_KEYS)
    opts = context.options
    agent = resolve_agent(opts, step)
    model = await resolve_model(opts, step, context, agent=agent)
    # Raw pipeline-op dispatches (the escape hatch documented in the package
    # docstring) bypass build_dispatch_body, so model_entity_id may not be
    # pre-populated in pipeline_options. Recompute the canonical id from the
    # resolved model in that case; admission-path dispatches always
    # pre-populate via service.py.
    model_entity_id = str(
        opts.get("model_entity_id") or canonical_model_entity_id(model)
    )
    provider = effective_provider_for_model(ModelId.parse(model).provider)
    publish = lambda event: handler._publish_bus_event(context, event)  # noqa: E731
    if agent is not None:
        check_agent_model_consistency(
            agent=agent,
            model=model,
            model_entity_id=model_entity_id,
            provider=provider,
            execution_id=context.execution_id,
            publish=publish,
        )
    mcp_enabled = bool(opts.get("mcp", True))
    remote_mcp = resolve_remote_mcp(
        opts=opts,
        step=step,
        context=context,
        provider=provider,
        model=model,
        model_entity_id=model_entity_id,
        agent=agent,
        mcp_enabled=mcp_enabled,
        publish=publish,
    )
    raw_turns = opts.get("max_tool_turns", step.get_domain_field("max_tool_turns", 10))
    max_turns = int(raw_turns)
    if max_turns < 1:
        raise ValueError(f"max_tool_turns must be >= 1, got {max_turns}")

    user_prompt = resolve_user_prompt(step, context)
    opt_tools = opts.get("tools")
    check_boot_provider_compatibility(
        agent=agent,
        model=model,
        provider=provider,
        mcp_enabled=mcp_enabled,
        opt_tools=opt_tools,
        execution_id=context.execution_id,
        publish=publish,
    )
    boot_profile = str(step.get_domain_field("boot_profile") or "light")
    tools, system, hydration_meta = await resolve_dispatch_tool_set(
        mcp_enabled=mcp_enabled,
        remote_mcp=remote_mcp,
        opt_tools=opt_tools,
        agent=agent,
        model=model,
        provider=provider,
        team_tool_names=handler._TEAM_TOOL_NAMES,
        endpoint_request_id=opts.get("_endpoint_request_id"),
        system_prompt=resolve_system_prompt(step, context),
        publish=publish,
        execution_id=context.execution_id,
        boot_profile=boot_profile,
    )

    if remote_mcp:
        publish(
            PipelineFrontierDispatchRemoteMcpEnabled(
                execution_id=context.execution_id,
                agent=agent,
                model=model,
                model_entity_id=model_entity_id,
                provider=provider,
            )
        )

    return AdmissionResult(
        opts=opts,
        agent=agent,
        model=model,
        model_entity_id=model_entity_id,
        provider=provider,
        publish=publish,
        mcp_enabled=mcp_enabled,
        remote_mcp=remote_mcp,
        max_turns=max_turns,
        user_prompt=user_prompt,
        opt_tools=opt_tools,
        boot_profile=boot_profile,
        tools=tools,
        system=system,
        hydration_meta=hydration_meta,
    )
