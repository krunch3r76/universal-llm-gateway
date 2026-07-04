"""Generation-parameter assembly and ``FrontierRequest`` construction.

Builds the merged generation-parameter dict (step defaults < caller
``generation_parameters``), applies the model-default ``reasoning_effort``,
translates ``reasoning_effort`` to a provider-native ``thinking`` config,
injects the card-derived server-side built-in tool set for agent dispatch when
the ``server_tools`` knob allows,
optionally appends the runtime-context block to the system prompt, resolves the
per-model ``max_output`` + reasoning at the SINGLE ``CapabilityDispatch``
boundary (G7), and assembles the :class:`~llm_adapters.FrontierRequest`. This is
the sole frontier-stack site that resolves ``max_output`` — adapters downstream
are pure consumers of the resolved ``req.max_tokens``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from llm_adapters import FrontierRequest
from llm_adapters.capability_dispatch import (
    CATALOG_MISS_EVENT,
    KNOB_REJECTED_EVENT,
    RESOLVED_EVENT,
    CatalogMissError,
    DispatchResolution,
    ProtocolError,
    resolve_dispatch,
)
from model_capabilities import server_side_tools
from model_id import ModelId

from ...events.dispatch import (
    PipelineFrontierCapabilityCatalogMiss,
    PipelineFrontierCapabilityKnobRejected,
    PipelineFrontierCapabilityResolved,
    PipelineFrontierDispatchToolSuppressed,
)
from ...execution.errors import (
    CapabilityCatalogMissError,
    CapabilityKnobRejectedError,
)
from .admission_checks import (
    build_runtime_context_block,
    prepend_dispatch_context,
)
from .request import (
    resolve_default_reasoning_effort,
    resolve_messages,
    translate_reasoning_effort,
)

if TYPE_CHECKING:
    from ..protocol import PipelineContext
    from ..schemas import StepConfig
    from .admission_gate import AdmissionResult
    from .handler import FrontierDispatchHandler


@dataclass
class FrontierRequestBundle:
    """The built ``FrontierRequest`` plus the finalized system prompt.

    ``system`` is returned alongside ``req`` because the StepOutput assembled in
    the completion phase records the exact system prompt sent to the provider
    (after the dispatch-context prepend and any runtime-context block applied
    here), which is not otherwise recoverable from the request object.
    """

    req: FrontierRequest
    system: str | None


def build_frontier_request(
    handler: FrontierDispatchHandler,
    step: StepConfig,
    context: PipelineContext,
    admission: AdmissionResult,
) -> FrontierRequestBundle:
    """Assemble generation parameters and the FrontierRequest for the loop.

    Generation-parameter precedence is step defaults overlaid by caller
    ``generation_parameters``; explicit caller ``reasoning_effort`` and
    ``thinking`` always win over the model defaults and the effort→thinking
    translation. Server-side built-in injection honors the ``server_tools`` knob
    and explicit ``provider_options.{provider}.tools`` override. ``max_tokens``
    is resolved at the single
    ``CapabilityDispatch`` boundary (:func:`_resolve_dispatch_boundary`) — the
    adapter receives a concrete resolved int and never re-defaults.
    """
    step_params, _ = handler._build_generation_params(step, resolved_config={})
    system = prepend_dispatch_context(admission.system)
    opt_params = admission.opts.get("generation_parameters") or {}
    if isinstance(opt_params, dict):
        gen_params: dict[str, Any] = {**step_params, **opt_params}
    else:
        gen_params = step_params

    # Apply model-specific default ``reasoning_effort`` when the caller did not
    # supply one. Centralized in ``resolve_default_reasoning_effort`` so every
    # dispatch path inherits the default uniformly. Treats both missing and
    # empty-string values as "unset" (the MCP wrapper passes
    # ``reasoning_effort or ""``, mirroring the translation gate below).
    # Explicit caller value — including non-default values like ``"medium"`` —
    # always wins.
    existing_effort = gen_params.get("reasoning_effort")
    if not (isinstance(existing_effort, str) and existing_effort):
        default_effort = resolve_default_reasoning_effort(admission.model)
        if default_effort is not None:
            gen_params["reasoning_effort"] = default_effort

    # Translate convenience ``reasoning_effort`` to a provider-native
    # ``thinking`` config when the caller did not supply ``thinking``
    # explicitly. Explicit ``thinking`` always wins. The raw ``reasoning_effort``
    # value is left in gen_params so the Anthropic adapter can also surface it as
    # ``output_config.effort`` alongside the enabled thinking config.
    effort_raw = gen_params.get("reasoning_effort")
    if isinstance(effort_raw, str) and effort_raw and not gen_params.get("thinking"):
        translated = translate_reasoning_effort(
            effort_raw,
            admission.provider,
            model=admission.model,
        )
        if translated is not None:
            gen_params["thinking"] = translated

    # For agent dispatches, inject server-side built-ins from the model card when
    # the ``server_tools`` knob allows (default ALL). Caller-supplied
    # ``generation_parameters.provider_options.{provider}.tools`` (including
    # ``[]`` to suppress) always wins via the ``if "tools" not in`` guard.
    if admission.agent:
        builtins = server_side_tools(admission.model)
        if builtins:
            po: dict[str, Any] = dict(gen_params.get("provider_options") or {})
            provider_opts: dict[str, Any] = dict(po.get(admission.provider) or {})
            if "tools" not in provider_opts:
                if admission.server_tools_enabled:
                    provider_opts["tools"] = [{"type": name} for name in builtins]
                else:
                    admission.publish(
                        PipelineFrontierDispatchToolSuppressed(
                            execution_id=context.execution_id,
                            agent=admission.agent,
                            model=admission.model,
                            provider=admission.provider,
                            reason="server_tools_knob",
                        )
                    )
            po[admission.provider] = provider_opts
            gen_params["provider_options"] = po

    if bool(step.get_domain_field("inject_runtime_context")):
        effort_str = gen_params.get("reasoning_effort") or "default"
        system = (system or "") + build_runtime_context_block(
            pipeline_id=context.pipeline.id,
            model=admission.model,
            reasoning_effort=effort_str,
            boot_profile=admission.boot_profile,
            max_turns=admission.max_turns,
        )

    # ── Single max-output + reasoning resolution boundary (G7) ──────────────
    # The ONE frontier-stack site that resolves per-model max_output. Pass the
    # FULL admission id (cloud ``ModelId.normalized`` == ``provider/model``, the
    # registry key) BEFORE ``api_model_id`` strips the provider for the adapter.
    # The boundary reproduces the OLD per-stack resolution (cross-knob budget
    # bump → default → floor-bump → ceiling-clamp) exactly (G8 parity); the
    # adapters consume the resolved ``req.max_tokens`` verbatim.
    thinking = gen_params.get("thinking")
    thinking_dict = thinking if isinstance(thinking, dict) else None
    effort_final = gen_params.get("reasoning_effort")
    effort_for_dispatch = (
        effort_final if isinstance(effort_final, str) and effort_final else None
    )
    requested_max = gen_params.get("max_tokens")
    requested_max = requested_max if isinstance(requested_max, int) else None
    resolution = _resolve_dispatch_boundary(
        admission=admission,
        context=context,
        requested_max=requested_max,
        thinking=thinking_dict,
        reasoning_effort=effort_for_dispatch,
    )

    wire_messages = resolve_messages(step, context, user_prompt=admission.user_prompt)
    req = FrontierRequest(
        messages=wire_messages,
        model=ModelId.parse(admission.model).api_model_id,
        system=system or "",
        max_tokens=resolution.max_output.resolved,
        temperature=gen_params.get("temperature"),
        top_p=gen_params.get("top_p"),
        seed=gen_params.get("seed"),
        stop_sequences=gen_params.get("stop"),
        thinking=thinking_dict,
        effort=gen_params.get("reasoning_effort"),
        tools=admission.tools or None,
        tool_choice=gen_params.get("tool_choice"),
        response_format=gen_params.get("response_format"),
        provider_options=gen_params.get("provider_options"),
        mcp_tool_loop=bool(admission.tools) and not admission.remote_mcp,
        remote_mcp=admission.remote_mcp,
        skills_mount=admission.skills_mount,
    )
    return FrontierRequestBundle(req=req, system=system)


def _resolve_dispatch_boundary(
    *,
    admission: AdmissionResult,
    context: PipelineContext,
    requested_max: int | None,
    thinking: dict[str, Any] | None,
    reasoning_effort: str | None,
) -> Any:
    """Resolve the per-model dispatch and emit the G2 observability event.

    Wraps the single ``resolve_dispatch`` boundary call with the two structural
    failure mappings the frontier handler owns:

    - **G9 live-flip** — ``ProtocolError`` (an unsupported declared knob) emits
      one ``capability_dispatch.knob_rejected`` event per ``KnobViolation`` and
      is re-raised as :class:`CapabilityKnobRejectedError` (4xx envelope).
    - **G13 fail-fast** — ``CatalogMissError`` (provider-uninferable model)
      emits ``capability_dispatch.catalog_miss`` and is re-raised as
      :class:`CapabilityCatalogMissError`.

    On success it emits ``capability_dispatch.resolved`` from
    ``resolution.resolved_event_fields()`` (G2 pinned) and returns the
    :class:`~llm_adapters.capability_dispatch.DispatchResolution`.
    """
    try:
        resolution = resolve_dispatch(
            admission.model,
            requested_max_output=requested_max,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )
    except ProtocolError as exc:
        for violation in exc.violations:
            admission.publish(
                PipelineFrontierCapabilityKnobRejected(
                    execution_id=context.execution_id,
                    event_name=KNOB_REJECTED_EVENT,
                    model=admission.model,
                    model_entity_id=admission.model_entity_id,
                    provider=admission.provider,
                    knob=violation.knob,
                    reject_code=violation.reject_code,
                    reason=violation.message,
                )
            )
        raise CapabilityKnobRejectedError(
            model=admission.model,
            provider=admission.provider,
            violations=[
                {"knob": v.knob, "reject_code": v.reject_code, "message": v.message}
                for v in exc.violations
            ],
        ) from exc
    except CatalogMissError as exc:
        admission.publish(
            PipelineFrontierCapabilityCatalogMiss(
                execution_id=context.execution_id,
                event_name=CATALOG_MISS_EVENT,
                model=admission.model,
                model_entity_id=admission.model_entity_id,
                miss_key=exc.miss_key,
                miss_reason=exc.miss_reason,
            )
        )
        raise CapabilityCatalogMissError(
            model=admission.model,
            miss_key=exc.miss_key,
            miss_reason=exc.miss_reason,
        ) from exc

    admission.publish(
        PipelineFrontierCapabilityResolved(
            execution_id=context.execution_id,
            event_name=RESOLVED_EVENT,
            model=admission.model,
            model_entity_id=admission.model_entity_id,
            provider=admission.provider,
            api_surface=resolution.api_surface,
            resolved_fields=_resolved_event_fields(resolution, reasoning_effort),
        )
    )
    return resolution


def _resolved_event_fields(
    resolution: DispatchResolution, reasoning_effort: str | None
) -> dict[str, Any]:
    resolved_fields = resolution.resolved_event_fields()
    if resolution.reasoning.value_kind == "adaptive" and reasoning_effort:
        resolved_fields = {
            **resolved_fields,
            "reasoning_output_config_effort": reasoning_effort,
        }
    return resolved_fields
