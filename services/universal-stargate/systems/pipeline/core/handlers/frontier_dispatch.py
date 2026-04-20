"""Built-in ``frontier_dispatch_v1`` step handler — native-endpoint frontier dispatch.

Routes pipeline dispatch calls directly to Stargate's provider-native
endpoints (Anthropic messages, OpenAI/xAI responses, Google generateContent)
via the in-process ``CloudProxyClient`` forwarder. Uses
``libs/agent_seat/native_loop`` for the bounded tool loop, shared with
MCP's ``frontier_generate``.

Persona is a runtime option:

- ``pipeline_options.agent`` ∈ {orion, oppie, bard, api_claude}: team-seat mode.
  Hydrates that agent's Cortex boot, injects birth prompt + team toolset.
  oppie → xAI, orion → OpenAI, bard → Google, api_claude → Anthropic.
  web (Claude Web) is not a valid agent — not reachable via API.
- ``agent`` omitted: persona-free mode. Raw native call. Optional
  read-only toolset via ``pipeline_options.mcp`` (default True).

MCP + remote_mcp semantics:

- ``pipeline_options.mcp`` (default ``True``) — gate on whether any MCP
  client-side tooling is available to the model for this call.
- ``pipeline_options.remote_mcp`` — meaningful only when ``mcp=True`` AND the
  resolved provider is ``anthropic`` (native ``mcp_toolset`` path). All other
  providers must have ``remote_mcp=False``; ``remote_mcp=True`` on a
  non-anthropic provider — or with ``mcp=False`` — is rejected structurally.

YAML shape::

    steps:
      - name: respond
        type: frontier_dispatch_v1
        # model_ref intentionally absent — caller provides via
        # pipeline_options.model.

Caller::

    pipeline(op="async", pipeline_id="frontier-dispatch",
             pipeline_options={"model": "openai/gpt-5.4", "agent": "orion"},
             messages=[{"role": "user", "content": "..."}])
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from agent_seat import (
    TEAM_TOOL_DEFINITIONS,
    TOOL_DEFINITIONS,
    assemble_system_prompt,
    hydrate_agent,
    resolve_tools,
)
from agent_seat.native_loop import NativeLoopResult, run_native_tool_loop
from llm_adapters import FrontierRequest, effective_provider_for_model
from model_id import ModelId
from universal_logging import get_logger

from ..events.dispatch import (
    PipelineFrontierDispatchCompleted,
    PipelineFrontierDispatchExhausted,
    PipelineFrontierDispatchHydrated,
    PipelineFrontierDispatchRemoteMcpEnabled,
    PipelineFrontierDispatchRemoteMcpMisconfigured,
    PipelineFrontierDispatchRemoteMcpUnsupported,
    PipelineFrontierDispatchStarted,
    PipelineFrontierDispatchToolCalled,
    PipelineFrontierDispatchToolFailed,
)
from ..execution.errors import RemoteMcpUnsupportedError
from ..execution.resolver import NamespaceResolver, traverse_path
from .builtin import BaseHandler
from .frontier_dispatch_observability import emit_post_loop_observability
from .protocol import StepOutput
from .registry import register_handler

if TYPE_CHECKING:
    from ..schemas import StepConfig
    from .protocol import PipelineContext

logger = get_logger(__name__)


@register_handler
class FrontierDispatchHandler(BaseHandler):
    """Native-endpoint frontier dispatch with persona-conditional hydration."""

    step_type: str = "frontier_dispatch_v1"

    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        opts = context.options
        model = self._resolve_model(opts, step)
        agent = self._resolve_agent(opts, step)
        provider = effective_provider_for_model(ModelId.parse(model).provider)
        mcp_enabled = bool(opts.get("mcp", True))
        remote_mcp = self._resolve_remote_mcp(
            opts=opts,
            step=step,
            context=context,
            provider=provider,
            model=model,
            agent=agent,
            mcp_enabled=mcp_enabled,
        )
        max_turns = int(
            opts.get("max_tool_turns", step.get_domain_field("max_tool_turns", 10))
            or 10
        )
        transcript_id_raw = opts.get("transcript_id") or step.get_domain_field(
            "transcript_id"
        )
        transcript_id: str | None = (
            str(transcript_id_raw) if transcript_id_raw is not None else None
        )

        user_prompt = self._resolve_user_prompt(step, context)

        # Tool injection matrix — see module docstring for the full semantics.
        # mcp=False                 → no tools
        # mcp=True,  remote_mcp=True  → no tools (anthropic server-side mcp_toolset)
        # mcp=True,  remote_mcp=False → inject client-side TOOL_DEFINITIONS
        opt_tools = opts.get("tools")
        skip_legacy_tier_block = False
        if isinstance(opt_tools, list):
            resolved_names = [str(name) for name in opt_tools if isinstance(name, str)]
            if len(resolved_names) != len(opt_tools):
                raise ValueError("pipeline_options.tools must be a list[str]")
            if not mcp_enabled or remote_mcp:
                tools: list[dict[str, Any]] = []
            else:
                tools = resolve_tools(resolved_names)
            system = self._resolve_system_prompt(step, context)
            hydration_meta = {"agent": None, "tool_resolution": "endpoint-supplied"}
            skip_legacy_tier_block = True

        if not skip_legacy_tier_block:
            if agent:
                bundle = await hydrate_agent(agent, transcript_id)
                self._publish_bus_event(
                    context,
                    PipelineFrontierDispatchHydrated(
                        agent=agent,
                        execution_id=context.execution_id,
                        briefing_bytes=bundle.section_counts.get("briefing_bytes", 0),
                        section_counts=bundle.section_counts,
                        continuation_id=bundle.continuation_id,
                    ),
                )
                system = assemble_system_prompt(
                    agent,
                    briefing_card_md=bundle.briefing_card_md,
                    continuation_md=bundle.continuation_md,
                    extra_system=step.system_prompt,
                )
                if not mcp_enabled or remote_mcp:
                    tools = []
                else:
                    tools = [*TOOL_DEFINITIONS, *TEAM_TOOL_DEFINITIONS]
                hydration_meta = {
                    "agent": agent,
                    "section_counts": bundle.section_counts,
                    "continuation_id": bundle.continuation_id,
                }
            else:
                system = self._resolve_system_prompt(step, context)
                if not mcp_enabled or remote_mcp:
                    tools = []
                else:
                    tools = list(TOOL_DEFINITIONS)
                hydration_meta = {"agent": None}

        if remote_mcp:
            self._publish_bus_event(
                context,
                PipelineFrontierDispatchRemoteMcpEnabled(
                    execution_id=context.execution_id,
                    agent=agent,
                    model=model,
                    provider=provider,
                ),
            )

        step_params, _ = self._build_generation_params(step, resolved_config={})
        opt_params = opts.get("generation_parameters") or {}
        if isinstance(opt_params, dict):
            gen_params: dict[str, Any] = {**step_params, **opt_params}
        else:
            gen_params = step_params

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
            mcp_tool_loop=bool(tools) and not remote_mcp,
            remote_mcp=remote_mcp,
        )

        send_native = self._build_in_process_sender(context, step.id)
        cancel_check = self._build_cancel_check(context)
        on_tool_event = self._build_on_tool_event(context, agent)

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
        except RuntimeError as exc:
            # resolve_mcp_env() raises when MCP_PUBLIC_URL/MCP_AUTH_TOKEN is
            # unset in the Stargate container env. Emit the dedicated signal
            # before bubbling, so pipeline_execution_failed carries a
            # structural-misconfiguration trail.
            if remote_mcp and "MCP_PUBLIC_URL" in str(exc):
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
        # short-circuit inside the detector.
        emit_post_loop_observability(
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
        }
        return output

    # Providers that support native remote MCP (server-side mcp_toolset).
    # Only anthropic has a production path today — openai/xai/google models
    # reach MCP tooling through client-side injection only. Attempting
    # ``remote_mcp=True`` on any other provider short-circuits before the
    # native call with ``RemoteMcpUnsupportedError``.
    _REMOTE_MCP_PROVIDERS: frozenset[str] = frozenset({"anthropic"})

    def _resolve_remote_mcp(
        self,
        *,
        opts: dict[str, Any],
        step: StepConfig,
        context: PipelineContext,
        provider: str,
        model: str,
        agent: str | None,
        mcp_enabled: bool,
    ) -> bool:
        """Resolve and validate ``remote_mcp`` against provider + mcp.

        Default: ``True`` iff ``provider=anthropic`` and ``mcp_enabled`` —
        otherwise ``False``. Explicit ``remote_mcp=True`` is rejected when
        either (a) ``mcp_enabled=False`` (remote_mcp requires mcp) or (b)
        the provider is not in ``_REMOTE_MCP_PROVIDERS`` (anthropic-only).
        Violations emit ``pipeline.frontier.dispatch.remotemcp.unsupported``
        and raise ``RemoteMcpUnsupportedError`` before hydration.
        """
        supports = provider in self._REMOTE_MCP_PROVIDERS
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
            self._publish_bus_event(
                context,
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

    def _resolve_model(self, opts: dict[str, Any], step: StepConfig) -> str:
        """Resolve model from ``pipeline_options.model`` or ``step.model_ref``."""
        model = opts.get("model") or step.model_ref
        if not model or model == "default":
            raise ValueError(
                f"Step '{step.id}': frontier_dispatch_v1 requires "
                "pipeline_options.model (e.g. 'openai/gpt-5.4', "
                "'anthropic/claude-opus-4-7', 'xai/grok-4-fast-reasoning', "
                "'google/gemini-2.5-pro')."
            )
        return str(model)

    def _resolve_agent(self, opts: dict[str, Any], step: StepConfig) -> str | None:
        """Resolve agent identity.

        Precedence: ``pipeline_options.agent`` > step domain field > None.
        """
        agent = opts.get("agent") or step.get_domain_field("agent")
        if agent is None:
            return None
        agent_str = str(agent).strip()
        return agent_str or None

    def _resolve_user_prompt(self, step: StepConfig, context: PipelineContext) -> str:
        binding = step.handler_inputs.get("text")
        if binding is None:
            return context.source_text
        resolver = NamespaceResolver(context)
        value = traverse_path(
            resolver.resolve(binding),
            binding.field_path,
            step_name=step.id,
            field_name="text",
            binding_repr=str(binding),
            resolver=resolver,
        )
        if isinstance(value, str):
            return value
        if value is None:
            return context.source_text
        return str(value)

    def _resolve_system_prompt(self, step: StepConfig, context: PipelineContext) -> str:
        """Persona-free system prompt.

        Precedence: ``pipeline_options.system`` > ``step.system_prompt`` >
        first system message in ``context.messages``. ``pipeline_options.system``
        is used by MCP ``frontier_generate`` when ``boot='mcp'``.
        """
        opt_system = context.options.get("system")
        if isinstance(opt_system, str) and opt_system:
            return opt_system
        if step.system_prompt:
            return step.system_prompt
        msgs = context.messages or []
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "system":
                content = m.get("content", "")
                if isinstance(content, str):
                    return content
        return ""

    def _build_in_process_sender(
        self,
        context: PipelineContext,
        step_id: str,
    ) -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
        """Construct a send_native closure using Stargate's in-process cloud client."""
        from systems.proxy.routers.cloud_passthrough import _get_cloud_forwarder

        execution_id = context.execution_id
        headers = {
            "X-Pipeline-Internal": "true",
            "X-Pipeline-Execution-Id": execution_id,
            "X-Pipeline-Step-Id": step_id,
        }

        async def _send(path: str, json_body: dict[str, Any]) -> dict[str, Any]:
            client = _get_cloud_forwarder()
            if client is None:
                raise RuntimeError(
                    "cloud_forwarder unavailable — Stargate proxy not initialized"
                )
            resp = await client.post_provider_native_json(
                path, json_body, headers=headers
            )
            if resp.status_code >= 400:
                preview = resp.text[:500] if resp.text else ""
                raise RuntimeError(f"provider-native {resp.status_code}: {preview}")
            return resp.json()

        return _send

    def _build_cancel_check(self, context: PipelineContext) -> Callable[[], bool]:
        """Poll the dispatch tracker for cancellation at tool-loop boundaries."""
        execution_id = context.execution_id
        proxy = getattr(context, "_proxy", None)

        def _check() -> bool:
            if proxy is None:
                return False
            tracker = getattr(proxy, "pipeline_dispatch_tracker", None)
            if tracker is None:
                return False
            record = tracker.get(execution_id)
            if record is None:
                return False
            return record.status == "cancelled"

        return _check

    def _build_on_tool_event(
        self,
        context: PipelineContext,
        agent: str | None,
    ) -> Callable[[str, dict[str, Any]], None]:
        """Translate lib-emitted tool events to Stargate factories."""
        execution_id = context.execution_id

        def _on(signal: str, payload: dict[str, Any]) -> None:
            provider = str(payload.get("provider", ""))
            if signal == "pipeline.frontier.dispatch.tool.called":
                event: Any = PipelineFrontierDispatchToolCalled(
                    agent=agent,
                    execution_id=execution_id,
                    tool_name=str(payload.get("tool_name", "")),
                    turn=int(payload.get("turn", 0)),
                    elapsed_ms=float(payload.get("elapsed_ms", 0.0)),
                    provider=provider,
                )
            else:
                event = PipelineFrontierDispatchToolFailed(
                    agent=agent,
                    execution_id=execution_id,
                    tool_name=str(payload.get("tool_name", "")),
                    turn=int(payload.get("turn", 0)),
                    elapsed_ms=float(payload.get("elapsed_ms", 0.0)),
                    error="tool returned error envelope",
                    provider=provider,
                )
            self._publish_bus_event(context, event)

        return _on

    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        if step.type != "frontier_dispatch_v1":
            errors.append(f"Step '{step.id}': expected type frontier_dispatch_v1")
        return errors
