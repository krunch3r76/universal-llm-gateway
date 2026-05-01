"""Built-in ``frontier_dispatch_v1`` step handler — native-endpoint frontier dispatch.

Routes pipeline dispatch calls to Stargate's provider-native endpoints
via the in-process ``CloudProxyClient`` forwarder. Uses
``libs/agent_seat/native_loop`` for the bounded tool loop.

Subsystems extracted into sibling modules:

- ``frontier_dispatch_admission`` — unknown-option rejection, remote-MCP
  resolution, agent/model consistency checks, context injection.
- ``frontier_dispatch_request`` — model/agent/prompt resolution, reasoning-effort
  translation.
- ``frontier_dispatch_tools`` — default tool tiers, xAI built-ins, 3-way
  tool-set resolution (endpoint-supplied / persona-bound / persona-free).
- ``frontier_dispatch_streaming`` — cancel check, native sender, tool-event
  dispatcher.
- ``frontier_dispatch_observability`` — post-loop anomaly detection.

YAML shape::

    steps:
      - name: respond
        type: frontier_dispatch_v1

Caller::

    pipeline(op="async", pipeline_id="frontier-dispatch",
             pipeline_options={"model": "openai/gpt-5.4", "agent": "orion"},
             messages=[{"role": "user", "content": "..."}])
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from agent_seat.native_loop import NativeLoopResult, run_native_tool_loop
from llm_adapters import FrontierRequest, effective_provider_for_model
from llm_adapters._mcp_entry import RemoteMcpEnvMissingError
from model_id import ModelId

from ..events.dispatch import (
    PipelineFrontierDispatchCompleted,
    PipelineFrontierDispatchExhausted,
    PipelineFrontierDispatchRemoteMcpEnabled,
    PipelineFrontierDispatchRemoteMcpMisconfigured,
    PipelineFrontierDispatchStarted,
)
from .builtin import BaseHandler
from .frontier_dispatch_admission import (
    check_agent_model_consistency,
    check_boot_provider_compatibility,
    prepend_dispatch_context,
    reject_unknown_runtime_options,
    resolve_remote_mcp,
)
from .frontier_dispatch_observability import emit_post_loop_observability
from .frontier_dispatch_request import (
    resolve_agent,
    resolve_model,
    resolve_system_prompt,
    resolve_user_prompt,
    translate_reasoning_effort,
)
from .frontier_dispatch_streaming import (
    build_cancel_check,
    build_in_process_sender,
    build_on_tool_event,
)
from .frontier_dispatch_tools import XAI_BUILTIN_TOOLS, resolve_dispatch_tool_set
from .protocol import StepOutput
from .registry import register_handler

if TYPE_CHECKING:
    from ..schemas import StepConfig
    from .protocol import PipelineContext


@register_handler
class FrontierDispatchHandler(BaseHandler):
    """Native-endpoint frontier dispatch with persona-conditional hydration."""

    step_type: str = "frontier_dispatch_v1"
    _READ_TOOL_NAMES: tuple[str, ...] = ("cortex", "rag")
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
            "agent",
            "mcp",
            "remote_mcp",
            "tools",
            "max_tool_turns",
            "transcript_id",
            "system",
            "generation_parameters",
            "_endpoint_request_id",
        }
    )

    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        reject_unknown_runtime_options(
            step, context, self._ACCEPTED_RUNTIME_OPTION_KEYS
        )
        opts = context.options
        model = await resolve_model(opts, step, context)
        agent = resolve_agent(opts, step)
        provider = effective_provider_for_model(ModelId.parse(model).provider)
        publish = lambda event: self._publish_bus_event(context, event)  # noqa: E731
        if agent is not None:
            check_agent_model_consistency(
                agent=agent,
                model=model,
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
            agent=agent,
            mcp_enabled=mcp_enabled,
            publish=publish,
        )
        raw_turns = opts.get(
            "max_tool_turns", step.get_domain_field("max_tool_turns", 10)
        )
        max_turns = int(raw_turns)
        if max_turns < 1:
            raise ValueError(f"max_tool_turns must be >= 1, got {max_turns}")
        transcript_id_raw = opts.get("transcript_id") or step.get_domain_field(
            "transcript_id"
        )
        transcript_id: str | None = (
            str(transcript_id_raw) if transcript_id_raw is not None else None
        )

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
        tools, system, hydration_meta = await resolve_dispatch_tool_set(
            mcp_enabled=mcp_enabled,
            remote_mcp=remote_mcp,
            opt_tools=opt_tools,
            agent=agent,
            model=model,
            provider=provider,
            transcript_id=transcript_id,
            read_tool_names=self._READ_TOOL_NAMES,
            team_tool_names=self._TEAM_TOOL_NAMES,
            endpoint_request_id=opts.get("_endpoint_request_id"),
            system_prompt=resolve_system_prompt(step, context),
            publish=publish,
            execution_id=context.execution_id,
        )

        if remote_mcp:
            publish(
                PipelineFrontierDispatchRemoteMcpEnabled(
                    execution_id=context.execution_id,
                    agent=agent,
                    model=model,
                    provider=provider,
                )
            )

        step_params, _ = self._build_generation_params(step, resolved_config={})
        system = prepend_dispatch_context(system)
        opt_params = opts.get("generation_parameters") or {}
        if isinstance(opt_params, dict):
            gen_params: dict[str, Any] = {**step_params, **opt_params}
        else:
            gen_params = step_params

        # Translate convenience ``reasoning_effort`` to a provider-native
        # ``thinking`` config when the caller did not supply ``thinking``
        # explicitly. Explicit ``thinking`` always wins. The raw
        # ``reasoning_effort`` value is left in gen_params so the
        # Anthropic adapter can also surface it as ``output_config.effort``
        # alongside the enabled thinking config.
        effort_raw = gen_params.get("reasoning_effort")
        if (
            isinstance(effort_raw, str)
            and effort_raw
            and not gen_params.get("thinking")
        ):
            translated = translate_reasoning_effort(effort_raw, provider)
            if translated is not None:
                gen_params["thinking"] = translated

        # For xAI agent dispatches, inject the server-side built-in tool set as
        # the default.  Two conditions suppress injection:
        #   (a) mcp=False — unified "no tools" signal; respected for xAI personas.
        #   (b) Caller supplied an explicit ``tools`` list via frontier_generate
        #       (isinstance(opt_tools, list)); their intent overrides the default.
        # Caller-supplied ``generation_parameters.provider_options.xai.tools``
        # (including ``[]`` to suppress) always wins via the ``if "tools" not in``
        # guard below.
        if agent and provider == "xai" and mcp_enabled and not isinstance(
            opt_tools, list
        ):
            po: dict[str, Any] = dict(gen_params.get("provider_options") or {})
            xai_opts: dict[str, Any] = dict(po.get("xai") or {})
            if "tools" not in xai_opts:
                xai_opts["tools"] = XAI_BUILTIN_TOOLS
            po["xai"] = xai_opts
            gen_params["provider_options"] = po

        thinking = gen_params.get("thinking")
        req = FrontierRequest(
            messages=[{"role": "user", "content": user_prompt}],
            model=ModelId.parse(model).api_model_id,
            system=system or "",
            max_tokens=gen_params.get("max_tokens"),
            temperature=gen_params.get("temperature"),
            top_p=gen_params.get("top_p"),
            seed=gen_params.get("seed"),
            stop_sequences=gen_params.get("stop"),
            thinking=thinking if isinstance(thinking, dict) else None,
            effort=gen_params.get("reasoning_effort"),
            tools=tools or None,
            tool_choice=gen_params.get("tool_choice"),
            response_format=gen_params.get("response_format"),
            provider_options=gen_params.get("provider_options"),
            mcp_tool_loop=bool(tools) and not remote_mcp,
            remote_mcp=remote_mcp,
        )

        cancel_check = build_cancel_check(context)
        send_native = build_in_process_sender(
            context, step.id, agent, publish=publish, cancel_check=cancel_check
        )
        on_tool_event = build_on_tool_event(context, agent, publish=publish)

        self._publish_bus_event(
            context,
            PipelineFrontierDispatchStarted(
                execution_id=context.execution_id,
                agent=agent,
                model=model,
                provider=provider,
                boot_level="team" if agent else "none",
                remote_mcp=remote_mcp,
            ),
        )

        call_start = time.monotonic()
        try:
            result: NativeLoopResult = await run_native_tool_loop(
                model=model,
                req=req,
                send_native=send_native,
                max_turns=max_turns,
                on_tool_event=on_tool_event,
                cancel_check=cancel_check,
            )
        except RemoteMcpEnvMissingError as exc:
            # resolve_mcp_env() raises when MCP_PUBLIC_URL/MCP_AUTH_TOKEN is
            # unset in the Stargate container env. Emit the dedicated signal
            # before bubbling, so pipeline_execution_failed carries a
            # structural-misconfiguration trail.
            self._publish_bus_event(
                context,
                PipelineFrontierDispatchRemoteMcpMisconfigured(
                    execution_id=context.execution_id,
                    agent=agent,
                    model=model,
                    reason=str(exc),
                ),
            )
            raise
        latency_ms = (time.monotonic() - call_start) * 1000.0

        if result.exhausted:
            self._publish_bus_event(
                context,
                PipelineFrontierDispatchExhausted(
                    agent=agent,
                    execution_id=context.execution_id,
                    turns_used=result.turns_used,
                    tool_calls_made=result.tool_calls_made,
                    provider=result.provider,
                ),
            )
        else:
            self._publish_bus_event(
                context,
                PipelineFrontierDispatchCompleted(
                    agent=agent,
                    execution_id=context.execution_id,
                    turns_used=result.turns_used,
                    tool_calls_made=result.tool_calls_made,
                    reasoning_present=result.reasoning is not None,
                    prompt_tokens=result.usage.get("input_tokens", 0),
                    completion_tokens=result.usage.get("output_tokens", 0),
                    provider=result.provider,
                ),
            )
        # Post-loop observability (Task-7 Phase 1 hoist). Emitted on both
        # exhausted and completed branches — the anomalies (short output,
        # termination shadow) are response-fact detections independent of
        # whether the tool loop hit max turns. Helpers gate internally on
        # boot_level / provider so persona-free or non-Gemini dispatches
        # short-circuit inside the detector. Returned hints are threaded
        # through StepOutput.json["hints"] so the executor can surface them
        # in the poll-result payload (see executor._extract_output_hints).
        anomaly_hints = emit_post_loop_observability(
            context=context,
            publish=lambda event: self._publish_bus_event(context, event),
            agent=agent,
            boot_level="team" if agent else "none",
            model=model,
            result=result,
        )

        tool_calls_payload: list[dict[str, Any]] = [
            {
                "turn": tc.turn,
                "name": tc.name,
                "arguments": tc.arguments,
                "result": tc.result,
                "ok": tc.ok,
                "elapsed_ms": round(tc.elapsed_ms, 1),
            }
            for tc in result.tool_calls
        ]

        output = StepOutput(
            raw=result.content,
            reasoning=result.reasoning,
            prompt_tokens=result.usage.get("input_tokens", 0),
            completion_tokens=result.usage.get("output_tokens", 0),
            latency_ms=latency_ms,
            model_id=model,
            step_id=step.id,
            system_prompt=system,
            user_prompt=user_prompt,
        )
        output.json = {
            "content": result.content,
            "tool_calls_made": result.tool_calls_made,
            "tool_calls": tool_calls_payload,
            "turns_used": result.turns_used,
            "exhausted": result.exhausted,
            "cancelled": result.cancelled,
            "provider": result.provider,
            "hydration": hydration_meta,
            "hints": anomaly_hints,
        }
        return output

    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        if step.type != "frontier_dispatch_v1":
            errors.append(f"Step '{step.id}': expected type frontier_dispatch_v1")
        return errors
