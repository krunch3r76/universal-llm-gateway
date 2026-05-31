"""Generation-parameter assembly and ``FrontierRequest`` construction.

Builds the merged generation-parameter dict (step defaults < caller
``generation_parameters``), applies the model-default ``reasoning_effort``,
translates ``reasoning_effort`` to a provider-native ``thinking`` config,
injects the xAI server-side built-in tool set for xAI agent dispatch,
optionally appends the runtime-context block to the system prompt, and
assembles the :class:`~llm_adapters.FrontierRequest`. Preserves the monolith's
merge precedence and injection guards exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from llm_adapters import FrontierRequest
from model_id import ModelId

from ..frontier_dispatch_admission import (
    build_runtime_context_block,
    prepend_dispatch_context,
)
from ..frontier_dispatch_request import (
    resolve_default_reasoning_effort,
    resolve_messages,
    translate_reasoning_effort,
)
from ..frontier_dispatch_tools import XAI_BUILTIN_TOOLS

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
    translation. The xAI built-in injection and the runtime-context block honor
    the same guards as the monolith.
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

    # For xAI agent dispatches, inject the server-side built-in tool set as the
    # default. Two conditions suppress injection:
    #   (a) mcp=False — unified "no tools" signal; respected for xAI personas.
    #   (b) Caller supplied an explicit ``tools`` list via frontier_dispatch
    #       (isinstance(opt_tools, list)); their intent overrides the default.
    # Caller-supplied ``generation_parameters.provider_options.xai.tools``
    # (including ``[]`` to suppress) always wins via the ``if "tools" not in``
    # guard below.
    if (
        admission.agent
        and admission.provider == "xai"
        and admission.mcp_enabled
        and not isinstance(admission.opt_tools, list)
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
            model=admission.model,
            reasoning_effort=effort_str,
            boot_profile=admission.boot_profile,
            max_turns=admission.max_turns,
        )

    thinking = gen_params.get("thinking")
    wire_messages = resolve_messages(step, context, user_prompt=admission.user_prompt)
    req = FrontierRequest(
        messages=wire_messages,
        model=ModelId.parse(admission.model).api_model_id,
        system=system or "",
        max_tokens=gen_params.get("max_tokens"),
        temperature=gen_params.get("temperature"),
        top_p=gen_params.get("top_p"),
        seed=gen_params.get("seed"),
        stop_sequences=gen_params.get("stop"),
        thinking=thinking if isinstance(thinking, dict) else None,
        effort=gen_params.get("reasoning_effort"),
        tools=admission.tools or None,
        tool_choice=gen_params.get("tool_choice"),
        response_format=gen_params.get("response_format"),
        provider_options=gen_params.get("provider_options"),
        mcp_tool_loop=bool(admission.tools) and not admission.remote_mcp,
        remote_mcp=admission.remote_mcp,
    )
    return FrontierRequestBundle(req=req, system=system)
