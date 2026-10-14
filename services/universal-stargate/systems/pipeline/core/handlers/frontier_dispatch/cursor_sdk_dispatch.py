"""Cursor SDK substrate path for ``frontier_dispatch_v1`` (journal-projector L2).

Calls ``dispatch_cursor_sdk_generate`` directly — not
``dispatch_cursor_sdk_generate_route`` (that path requires a FastAPI
``Response`` and returns a 202 admit envelope without model text).
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, cast

from model_id import ModelId, canonical_model_entity_id
from universal_logging import get_logger

from ...events.dispatch import (
    PipelineFrontierDispatchCompleted,
    PipelineFrontierDispatchStarted,
)
from ..protocol import StepOutput
from .request import resolve_system_prompt, resolve_user_prompt

if TYPE_CHECKING:
    from ..protocol import PipelineContext
    from ..schemas import StepConfig
    from .admission_gate import AdmissionResult
    from .handler import FrontierDispatchHandler

logger = get_logger(__name__)

_PROJECTOR_ROLE = "cursor-sdk"
_PROJECTOR_LANE = "B"
_PROJECTOR_CONTRACT = "freeform"


def is_cursor_sdk_dispatch_model(model: str) -> bool:
    """True when ``model`` parses to the ``cursor_sdk`` backend."""
    try:
        return ModelId.parse(model).backend_type == "cursor_sdk"
    except (TypeError, ValueError):
        return False


def _resolve_model_knobs(
    opts: dict[str, Any], step: StepConfig
) -> dict[str, str] | None:
    raw = opts.get("model_knobs")
    if raw is None:
        raw = step.get_domain_field("model_knobs")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"model_knobs must be a dict, got {type(raw).__name__}")
    out: dict[str, str] = {}
    for key, value in raw.items():
        out[str(key)] = str(value)
    return out or None


def build_cursor_sdk_admission_result(
    handler: FrontierDispatchHandler,
    step: StepConfig,
    context: PipelineContext,
    *,
    model: str,
    opts: dict[str, Any],
    role: str | None,
) -> AdmissionResult:
    from .admission_gate import AdmissionResult

    user_prompt = resolve_user_prompt(step, context)
    system = resolve_system_prompt(step, context) or None
    if not (user_prompt or "").strip() and not (system or "").strip():
        raise ValueError(
            f"Step '{step.id}': cursor SDK dispatch requires non-empty prompt text."
        )
    model_entity_id = str(
        opts.get("model_entity_id") or canonical_model_entity_id(model)
    )
    publish = lambda event: handler._publish_bus_event(context, event)  # noqa: E731
    return AdmissionResult(
        opts=opts,
        agent=role,
        model=model,
        model_entity_id=model_entity_id,
        provider="cursor",
        publish=publish,
        mcp_enabled=False,
        server_tools_enabled=False,
        remote_mcp=None,
        max_turns=1,
        user_prompt=user_prompt,
        boot_profile=str(step.get_domain_field("boot_profile") or "light"),
        tools=None,
        system=system,
        hydration_meta={"agent": role},
        skills_mount=opts.get("skills_mount"),
    )


def build_cursor_sdk_step_output(
    *,
    admit: dict[str, Any],
    step: StepConfig,
    admission: AdmissionResult,
    latency_ms: float,
) -> StepOutput:
    """Map the SDK admit envelope to ``StepOutput`` (202-shaped, not journal text)."""
    raw_hint = json.dumps(
        {
            "execution_id": admit.get("execution_id"),
            "poll_hint": admit.get("poll_hint"),
        },
        sort_keys=True,
    )
    return StepOutput(
        raw=raw_hint,
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=latency_ms,
        model_id=admission.model,
        step_id=step.id,
        system_prompt=admission.system,
        user_prompt=admission.user_prompt,
        model_call_count=0,
        json={
            **admit,
            "provider": "cursor",
            "substrate": "cursor_sdk",
            "model_entity_id": admission.model_entity_id,
        },
    )


async def run_cursor_sdk_dispatch(
    handler: FrontierDispatchHandler,
    step: StepConfig,
    context: PipelineContext,
    admission: AdmissionResult,
) -> StepOutput:
    """Admit cursor-sdk generate; return poll envelope in ``StepOutput.json``."""
    from systems.frontier_consult.cursor_sdk_generate import (
        dispatch_cursor_sdk_generate,
    )

    model = admission.model
    opts = admission.opts
    publish = admission.publish
    pipeline_domain = getattr(getattr(context, "pipeline", None), "domain", None)
    if pipeline_domain == "journal_projector":
        from pipelines.journal_projector.v1.handlers.projector_guard import (
            reject_projector_wire_model,
        )

        reject_projector_wire_model(model)
    role = (
        (admission.agent or "").strip()
        or str(step.get_domain_field("role") or "").strip()
        or _PROJECTOR_ROLE
    )
    model_knobs = _resolve_model_knobs(opts, step)
    prompt_text = admission.user_prompt
    if admission.system:
        prompt_text = f"{admission.system}\n\n---\n\n{admission.user_prompt}"

    publish(
        PipelineFrontierDispatchStarted(
            execution_id=context.execution_id,
            agent=role,
            model=model,
            model_entity_id=admission.model_entity_id,
            provider="cursor",
            boot_level="none",
            remote_mcp=False,
            op=opts.get("op", ""),
            endpoint_request_id=opts.get("_endpoint_request_id"),
        )
    )

    started = time.monotonic()
    admit = await dispatch_cursor_sdk_generate(
        request_id=context.execution_id,
        role=role,
        model=model,
        subject=None,
        caller_agent=opts.get("caller_agent"),
        contract=cast(Any, _PROJECTOR_CONTRACT),
        packet_path=None,
        message_text=prompt_text,
        model_knobs=model_knobs,
        lane=_PROJECTOR_LANE,
        work_key=opts.get("work_key"),
    )
    latency_ms = (time.monotonic() - started) * 1000.0

    publish(
        PipelineFrontierDispatchCompleted(
            agent=role,
            execution_id=context.execution_id,
            turns_used=0,
            tool_calls_made=0,
            reasoning_present=False,
            prompt_tokens=0,
            completion_tokens=0,
            provider="cursor",
            model_entity_id=admission.model_entity_id,
            op=opts.get("op", ""),
        )
    )
    return build_cursor_sdk_step_output(
        admit=admit,
        step=step,
        admission=admission,
        latency_ms=latency_ms,
    )
