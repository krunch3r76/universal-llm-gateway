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

    # Public surface (preferred):
    team_dispatch(op="generate", role="gatherer", model="openai/gpt-5.4",
                  messages=[{"role": "user", "content": "..."}])
    frontier_dispatch(op="generate", model="openai/gpt-5.4",
                      messages=[{"role": "user", "content": "..."}])

    # Raw escape hatch (advanced — bypasses role admission):
    pipeline(op="async", pipeline_id="frontier-dispatch",
             pipeline_options={"model": "openai/gpt-5.4", "role": "gatherer"},
             messages=[{"role": "user", "content": "..."}])
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from agent_seat.native_loop import NativeLoopResult, run_native_tool_loop
from llm_adapters import FrontierRequest, effective_provider_for_model
from llm_adapters._mcp_entry import RemoteMcpEnvMissingError
from model_id import ModelId, canonical_model_entity_id

from ..events.dispatch import (
    PipelineFrontierDispatchCompleted,
    PipelineFrontierDispatchEmptyCompletion,
    PipelineFrontierDispatchExhausted,
    PipelineFrontierDispatchRemoteMcpEnabled,
    PipelineFrontierDispatchRemoteMcpMisconfigured,
    PipelineFrontierDispatchStarted,
)
from ..execution.errors import EmptyCompletionError, FrontierDispatchExhaustedError
from .builtin import BaseHandler
from .frontier_dispatch_admission import (
    build_runtime_context_block,
    check_agent_model_consistency,
    check_boot_provider_compatibility,
    prepend_dispatch_context,
    reject_unknown_runtime_options,
    resolve_remote_mcp,
    validate_frontier_dispatch_step,
)
from .frontier_dispatch_observability import emit_post_loop_observability
from .frontier_dispatch_request import (
    resolve_agent,
    resolve_default_reasoning_effort,
    resolve_messages,
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
        reject_unknown_runtime_options(
            step, context, self._ACCEPTED_RUNTIME_OPTION_KEYS
        )
        opts = context.options
        agent = resolve_agent(opts, step)
        model = await resolve_model(opts, step, context, agent=agent)
        # Raw pipeline-op dispatches (the escape hatch documented in this
        # file's module docstring) bypass build_dispatch_body, so
        # model_entity_id may not be pre-populated in pipeline_options.
        # Recompute the canonical id from the resolved model in that case;
        # admission-path dispatches always pre-populate via service.py.
        model_entity_id = str(
            opts.get("model_entity_id") or canonical_model_entity_id(model)
        )
        provider = effective_provider_for_model(ModelId.parse(model).provider)
        publish = lambda event: self._publish_bus_event(context, event)  # noqa: E731
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
        raw_turns = opts.get(
            "max_tool_turns", step.get_domain_field("max_tool_turns", 10)
        )
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
            team_tool_names=self._TEAM_TOOL_NAMES,
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

        step_params, _ = self._build_generation_params(step, resolved_config={})
        system = prepend_dispatch_context(system)
        opt_params = opts.get("generation_parameters") or {}
        if isinstance(opt_params, dict):
            gen_params: dict[str, Any] = {**step_params, **opt_params}
        else:
            gen_params = step_params

        # Apply model-specific default ``reasoning_effort`` when the caller
        # did not supply one. Centralized in ``resolve_default_reasoning_effort``
        # so every dispatch path inherits the default uniformly. Treats both
        # missing and empty-string values as "unset" (the MCP wrapper passes
        # ``reasoning_effort or ""``, mirroring the existing translation gate
        # below). Explicit caller value — including non-default values like
        # ``"medium"`` — always wins.
        existing_effort = gen_params.get("reasoning_effort")
        if not (isinstance(existing_effort, str) and existing_effort):
            default_effort = resolve_default_reasoning_effort(model)
            if default_effort is not None:
                gen_params["reasoning_effort"] = default_effort

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
            translated = translate_reasoning_effort(
                effort_raw,
                provider,
                model=model,
            )
            if translated is not None:
                gen_params["thinking"] = translated

        # For xAI agent dispatches, inject the server-side built-in tool set as
        # the default.  Two conditions suppress injection:
        #   (a) mcp=False — unified "no tools" signal; respected for xAI personas.
        #   (b) Caller supplied an explicit ``tools`` list via frontier_dispatch
        #       (isinstance(opt_tools, list)); their intent overrides the default.
        # Caller-supplied ``generation_parameters.provider_options.xai.tools``
        # (including ``[]`` to suppress) always wins via the ``if "tools" not in``
        # guard below.
        if (
            agent
            and provider == "xai"
            and mcp_enabled
            and not isinstance(opt_tools, list)
        ):
            po: dict[str, Any] = dict(gen_params.get("provider_options") or {})
            xai_opts: dict[str, Any] = dict(po.get("xai") or {})
            if "tools" not in xai_opts:
                xai_opts["tools"] = XAI_BUILTIN_TOOLS
            po["xai"] = xai_opts
            gen_params["provider_options"] = po

        if bool(step.get_domain_field("inject_runtime_context")):
            effort_str = gen_params.get("reasoning_effort") or "default"
            system = (system or "") + build_runtime_context_block(
                pipeline_id=context.pipeline.id,
                model=model,
                reasoning_effort=effort_str,
                boot_profile=boot_profile,
                max_turns=max_turns,
            )

        thinking = gen_params.get("thinking")
        wire_messages = resolve_messages(step, context, user_prompt=user_prompt)
        req = FrontierRequest(
            messages=wire_messages,
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
                model_entity_id=model_entity_id,
                provider=provider,
                boot_level="team" if agent else "none",
                remote_mcp=remote_mcp,
                op=opts.get("op", ""),
            ),
        )

        call_start = time.monotonic()
        try:
            result: NativeLoopResult = await run_native_tool_loop(
                model=model,
                req=req,
                send_native=send_native,
                agent=agent,
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
                    model_entity_id=model_entity_id,
                    reason=str(exc),
                ),
            )
            raise
        latency_ms = (time.monotonic() - call_start) * 1000.0

        finish_reason = getattr(result, "finish_reason", None)
        block_reason = getattr(result, "block_reason", None)
        exhaustion_summary = getattr(result, "exhaustion_summary", None)
        if isinstance(exhaustion_summary, dict):
            exhaustion_summary = {
                **exhaustion_summary,
                "execution_id": context.execution_id,
            }
        if result.exhausted:
            self._publish_bus_event(
                context,
                PipelineFrontierDispatchExhausted(
                    agent=agent,
                    execution_id=context.execution_id,
                    turns_used=result.turns_used,
                    tool_calls_made=result.tool_calls_made,
                    provider=result.provider,
                    model_entity_id=model_entity_id,
                    op=opts.get("op", ""),
                    finish_reason=finish_reason,
                    block_reason=block_reason,
                    enforcement="client",
                    exhaustion_summary=exhaustion_summary,
                ),
            )
            if not (result.content or "").strip():
                raise FrontierDispatchExhaustedError(
                    execution_id=context.execution_id,
                    agent=agent,
                    model=model,
                    provider=result.provider,
                    turns_used=result.turns_used,
                    tool_calls_made=result.tool_calls_made,
                    finish_reason=finish_reason,
                    block_reason=block_reason,
                    exhaustion_summary=exhaustion_summary,
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
                    model_entity_id=model_entity_id,
                    op=opts.get("op", ""),
                    finish_reason=finish_reason,
                    block_reason=block_reason,
                ),
            )
            # F3: detect silent empty-completion on the non-exhausted branch
            # and convert terminal state to failed. Exhausted empty content is
            # handled above as a distinct tool-loop budget failure. Originally
            # surfaced by Orion execution d65c723b (Cortex assertion 7903).
            #
            # Sub-case: a provider-managed tool loop (remote-MCP, or any
            # loop where the provider stops on its own ceiling) returns
            # ``content=""`` with ``finish_reason in {tool_calls, length}``.
            # The native loop never sets ``result.exhausted`` (it only saw
            # one provider round-trip), so without finish_reason inspection
            # this looks like a generic empty completion. Re-route to the
            # ``exhausted`` signal with ``enforcement="provider"`` so traces
            # are queryable as ceiling hits — observed on execution
            # ``e07481c4`` (todo:frontier-dispatch-empty-content-exhaustion).
            if not (result.content or "").strip():
                ceiling_finish_reasons = {"tool_calls", "tool_use", "length"}
                if finish_reason in ceiling_finish_reasons:
                    self._publish_bus_event(
                        context,
                        PipelineFrontierDispatchExhausted(
                            agent=agent,
                            execution_id=context.execution_id,
                            turns_used=result.turns_used,
                            tool_calls_made=result.tool_calls_made,
                            provider=result.provider,
                            model_entity_id=model_entity_id,
                            op=opts.get("op", ""),
                            finish_reason=finish_reason,
                            block_reason=block_reason,
                            enforcement="provider",
                            exhaustion_summary=exhaustion_summary,
                        ),
                    )
                else:
                    self._publish_bus_event(
                        context,
                        PipelineFrontierDispatchEmptyCompletion(
                            execution_id=context.execution_id,
                            agent=agent,
                            model=model,
                            model_entity_id=model_entity_id,
                            provider=result.provider,
                            turns_used=result.turns_used,
                            tool_calls_made=result.tool_calls_made,
                            finish_reason=finish_reason,
                            block_reason=block_reason,
                        ),
                    )
                raise EmptyCompletionError(
                    execution_id=context.execution_id,
                    agent=agent,
                    model=model,
                    provider=result.provider,
                    turns_used=result.turns_used,
                    finish_reason=finish_reason,
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
            "model_entity_id": model_entity_id,
            "finish_reason": finish_reason,
            "block_reason": block_reason,
            "exhaustion_summary": exhaustion_summary,
            "hydration": hydration_meta,
            "hints": anomaly_hints,
            "reasoning": result.reasoning,
            "raw_response": result.raw,
        }
        return output

    def validate(self, step: StepConfig) -> list[str]:
        return validate_frontier_dispatch_step(step)
