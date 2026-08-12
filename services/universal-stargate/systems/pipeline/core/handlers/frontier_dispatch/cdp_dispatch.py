"""CDP substrate path for ``frontier_dispatch_v1`` (pipeline Option 3).

Synchronous poll-to-proof via ``run_cdp_generate``; maps ``CdpGenerateResult``
to dual-bind ``StepOutput`` (inline + proof URIs when present).

Harvest posture: Cowork paths (`auto`, `output-file`) are operational default.
``harvest_source=chat`` is wire-stub only (future small-work interface) — see
``notes/system/specs/substrate-apis-cdp-cursor.md`` § Chat harvest stub; ¬ steer
skills/packets toward chat harvest yet.

``pipeline_options.skills`` (optional list): forwarded to ``run_cdp_generate``;
``shared_sync`` slugs stage as leading ``/<slug>\\n`` manifest and attach via
composer **+ → Skills → pick** at satellite runtime.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Literal

from claude_bundles.cdp_model_endpoint import (
    DEFAULT_MAX_WALL_S,
    CdpGenerateResult,
    run_cdp_generate,
)
from model_id import ModelId, canonical_model_entity_id

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

HarvestSource = Literal["chat", "output-file", "auto"]
ExpectedSize = Literal["small", "large", "auto"]

_VALID_HARVEST_SOURCES = frozenset({"chat", "output-file", "auto"})
_VALID_EXPECTED_SIZES = frozenset({"small", "large", "auto"})


class CdpDispatchError(ValueError):
    """CDP generate failed or stalled without a successful step outcome."""


def is_cdp_dispatch_model(model: str) -> bool:
    """True when ``model`` is ``cdp/<picker>``."""
    try:
        return ModelId.parse(model).backend_type == "cdp"
    except (TypeError, ValueError):
        return False


def reject_cdp_role_conflict(
    *,
    role: str | None,
    model: str,
    execution_id: str,
) -> None:
    """Reject role + ``cdp/`` combinations (role would be dropped on cloud path)."""
    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.cdp_generate import reject_role_with_substrate_model

    if not role:
        return
    try:
        reject_role_with_substrate_model(
            role=role,
            model=model,
            request_id=execution_id,
        )
    except FrontierEndpointError as exc:
        raise ValueError(exc.reason) from exc


def parse_cdp_harvest_options(opts: dict[str, Any]) -> dict[str, Any]:
    """Extract CDP harvest knobs from ``pipeline_options``."""
    harvest_source = opts.get("harvest_source", "auto")
    if harvest_source not in _VALID_HARVEST_SOURCES:
        raise ValueError(
            f"harvest_source={harvest_source!r} must be one of: "
            f"{sorted(_VALID_HARVEST_SOURCES)}"
        )
    expected_size = opts.get("expected_size", "auto")
    if expected_size not in _VALID_EXPECTED_SIZES:
        raise ValueError(
            f"expected_size={expected_size!r} must be one of: "
            f"{sorted(_VALID_EXPECTED_SIZES)}"
        )
    download_output = bool(opts.get("download_output", False))
    raw_timeout = opts.get("timeout_seconds")
    max_wall_s = (
        float(raw_timeout)
        if isinstance(raw_timeout, int | float) and raw_timeout > 0
        else DEFAULT_MAX_WALL_S
    )
    return {
        "harvest_source": harvest_source,
        "expected_size": expected_size,
        "download_output": download_output,
        "max_wall_s": max_wall_s,
    }


def compose_cdp_prompt_text(user_prompt: str, system_prompt: str | None) -> str:
    """Merge optional system block with the resolved user prompt."""
    user = (user_prompt or "").strip()
    system = (system_prompt or "").strip()
    if not user and not system:
        return ""
    if system and user:
        return f"{system}\n\n---\n\n{user}"
    return user or system


def build_cdp_step_output(
    *,
    result: CdpGenerateResult,
    step: StepConfig,
    admission: AdmissionResult,
    latency_ms: float,
    system_prompt: str | None,
) -> StepOutput:
    """Map adapter result to dual-bind ``StepOutput``."""
    output = StepOutput(
        raw=result.body,
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=latency_ms,
        model_id=admission.model,
        step_id=step.id,
        system_prompt=system_prompt,
        user_prompt=admission.user_prompt,
        model_call_count=1,
    )
    json_payload: dict[str, Any] = {
        "content": result.body,
        "provider": "cdp",
        "substrate": result.substrate,
        "execution_id": result.execution_id,
        "satellite_execution_id": result.satellite_execution_id,
        "picker_model": result.picker_model,
        "prompt_uri": result.prompt_uri,
        "cost_source": result.cost_source,
        "model_entity_id": admission.model_entity_id,
        "poll_snapshots": result.poll_snapshots,
    }
    if result.archive_uri:
        json_payload["archive_uri"] = result.archive_uri
    if result.content_proof_uri:
        json_payload["content_proof_uri"] = result.content_proof_uri
    if result.content_proof_sha256:
        json_payload["content_proof_sha256"] = result.content_proof_sha256
    harvest_provenance = result.extras.get("harvest_provenance")
    if harvest_provenance:
        json_payload["harvest_provenance"] = harvest_provenance
    output.json = json_payload
    return output


def build_cdp_admission_result(
    handler: FrontierDispatchHandler,
    step: StepConfig,
    context: PipelineContext,
    *,
    model: str,
    opts: dict[str, Any],
    role: str | None,
) -> AdmissionResult:
    """Lightweight admission for ``cdp/`` — skips cloud MCP/hydration/tool-set."""
    from .admission_gate import AdmissionResult

    reject_cdp_role_conflict(
        role=role,
        model=model,
        execution_id=context.execution_id,
    )
    user_prompt = resolve_user_prompt(step, context)
    system = resolve_system_prompt(step, context) or None
    if not compose_cdp_prompt_text(user_prompt, system):
        raise ValueError(
            f"Step '{step.id}': CDP dispatch requires non-empty prompt text "
            "(user binding, source_text, or pipeline_options.system)."
        )
    model_entity_id = str(
        opts.get("model_entity_id") or canonical_model_entity_id(model)
    )
    publish = lambda event: handler._publish_bus_event(context, event)  # noqa: E731
    return AdmissionResult(
        opts=opts,
        agent=None,
        model=model,
        model_entity_id=model_entity_id,
        provider="cdp",
        publish=publish,
        mcp_enabled=False,
        server_tools_enabled=False,
        remote_mcp=None,
        max_turns=1,
        user_prompt=user_prompt,
        boot_profile=str(step.get_domain_field("boot_profile") or "light"),
        tools=None,
        system=system,
        hydration_meta={"agent": None},
        skills_mount=opts.get("skills_mount"),
    )


async def run_cdp_dispatch(
    handler: FrontierDispatchHandler,
    step: StepConfig,
    context: PipelineContext,
    admission: AdmissionResult,
) -> StepOutput:
    """Run synchronous CDP generate and return dual-bind ``StepOutput``."""
    from systems.frontier_consult.cdp_events import (
        CdpGenerateProof,
        CdpGenerateStalled,
        CdpGenerateSubmitted,
        publish_cdp_kwargs,
    )

    model = admission.model
    opts = admission.opts
    publish = admission.publish
    harvest = parse_cdp_harvest_options(opts)
    prompt_text = compose_cdp_prompt_text(admission.user_prompt, admission.system)
    skills_raw = opts.get("skills")
    skills = skills_raw if isinstance(skills_raw, list) else None
    request_id = context.execution_id

    publish(
        PipelineFrontierDispatchStarted(
            execution_id=context.execution_id,
            agent=None,
            model=model,
            model_entity_id=admission.model_entity_id,
            provider="cdp",
            boot_level="none",
            remote_mcp=False,
            op=opts.get("op", ""),
            endpoint_request_id=opts.get("_endpoint_request_id"),
        )
    )

    loop = asyncio.get_running_loop()
    submitted_sat_id: str | None = None

    def _on_submitted(satellite_execution_id: str) -> None:
        nonlocal submitted_sat_id
        submitted_sat_id = satellite_execution_id

        def _publish() -> None:
            publish_cdp_kwargs(
                CdpGenerateSubmitted,
                request_id=request_id,
                execution_id=context.execution_id,
                satellite_execution_id=satellite_execution_id,
                model=model,
            )

        loop.call_soon_threadsafe(_publish)

    started = time.monotonic()
    result = await asyncio.to_thread(
        run_cdp_generate,
        execution_id=context.execution_id,
        model_id=model,
        prompt_text=prompt_text,
        skills=skills,
        max_wall_s=harvest["max_wall_s"],
        harvest_source=harvest["harvest_source"],
        expected_size=harvest["expected_size"],
        download_output=harvest["download_output"],
        holder="frontier-dispatch-v1",
        converse=True,
        on_submitted=_on_submitted,
    )
    latency_ms = (time.monotonic() - started) * 1000.0

    if result.ok:
        publish_cdp_kwargs(
            CdpGenerateProof,
            request_id=request_id,
            execution_id=result.execution_id,
            satellite_execution_id=result.satellite_execution_id,
            archive_uri=result.archive_uri,
            content_proof_uri=result.content_proof_uri,
        )
        publish(
            PipelineFrontierDispatchCompleted(
                agent=None,
                execution_id=context.execution_id,
                turns_used=1,
                tool_calls_made=0,
                reasoning_present=False,
                prompt_tokens=0,
                completion_tokens=0,
                provider="cdp",
                model_entity_id=admission.model_entity_id,
                op=opts.get("op", ""),
            )
        )
        return build_cdp_step_output(
            result=result,
            step=step,
            admission=admission,
            latency_ms=latency_ms,
            system_prompt=admission.system,
        )

    publish_cdp_kwargs(
        CdpGenerateStalled,
        request_id=request_id,
        execution_id=result.execution_id,
        satellite_execution_id=result.satellite_execution_id or submitted_sat_id,
        stall_stage=result.stall_stage,
        error=result.error,
        since_last_progress_s=(result.extras or {}).get("since_last_progress_s"),
    )
    raise CdpDispatchError(
        f"CDP dispatch failed: stall_stage={result.stall_stage!r} "
        f"error={result.error!r} model={model!r}"
    )
