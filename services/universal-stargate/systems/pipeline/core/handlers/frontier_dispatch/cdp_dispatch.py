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
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from cdp_ask.unverifiable import WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED
from claude_bundles.cdp_model_endpoint import (
    DEFAULT_MAX_WALL_S,
    CdpGenerateResult,
    run_cdp_generate,
)
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

HarvestSource = Literal["chat", "output-file", "auto"]
ExpectedSize = Literal["small", "large", "auto"]

_VALID_HARVEST_SOURCES = frozenset({"chat", "output-file", "auto"})
_VALID_EXPECTED_SIZES = frozenset({"small", "large", "auto"})


class CdpDispatchError(ValueError):
    """CDP generate failed or stalled without a successful step outcome.

    Raised by ``run_cdp_dispatch`` after it publishes ``CdpGenerateStalled``; the
    message carries the adapter's ``stall_stage``, error and model. Subclasses
    ``ValueError`` so existing step-failure handling treats it as a bad outcome.
    """


def is_cdp_dispatch_model(model: str) -> bool:
    """Return True when ``model`` parses to the ``cdp`` backend (``cdp/<picker>``).

    Routing predicate used by the admission gate and FrontierDispatchHandler to
    divert to the CDP substrate path; unparseable model strings return False.
    """
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
    """Extract and validate CDP harvest knobs from ``pipeline_options``.

    Returns ``harvest_source`` and ``expected_size`` (each default ``auto``),
    ``download_output`` (bool) and ``max_wall_s`` (positive ``timeout_seconds`` or
    ``DEFAULT_MAX_WALL_S``). Raises ``ValueError`` on an unknown source or size.
    """
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
    """Merge the optional system block with the resolved user prompt into one text.

    Both parts are stripped; when both exist they are joined as system, a ``---``
    rule, then user. Returns whichever part is non-empty, or ``""`` when both are
    empty (admission treats that as an error).
    """
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
    """Map a successful CdpGenerateResult to a dual-bind ``StepOutput``.

    ``raw`` holds the harvested body (token counts are zero, one model call); ``json``
    carries provider/substrate ids, prompt URI, cost source and poll snapshots, plus
    archive/content-proof URIs, chat_url, registration_id and harvest provenance
    when the adapter supplied them.
    """
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
    if result.extras.get("chat_url"):
        json_payload["chat_url"] = result.extras["chat_url"]
    if result.extras.get("registration_id"):
        json_payload["registration_id"] = result.extras["registration_id"]
    harvest_provenance = result.extras.get("harvest_provenance")
    if harvest_provenance:
        json_payload["harvest_provenance"] = harvest_provenance
    output.json = json_payload
    return output


def lineage_leg_key(context: Any, step: Any) -> str:
    """Ledger key for one CDP step. Never an empty or ``None`` root.

    Prefers ``context.step_idempotency_key`` (S5). A missing method uses the
    same ``{lineage_root or execution_id}:{step.id}`` formula. An empty root
    or the literal ``None`` falls back to ``execution_id`` so two chat runs
    cannot share a leg.
    """
    key_fn = getattr(context, "step_idempotency_key", None)
    raw = key_fn(step) if callable(key_fn) else None
    step_id = getattr(step, "id", "")
    if not isinstance(raw, str) or not raw:
        root = getattr(context, "lineage_root", None) or getattr(
            context, "execution_id", ""
        )
        raw = f"{root}:{step_id}"
    root, _sep, _rest = raw.partition(":")
    if root in ("", "None"):
        exec_id = getattr(context, "execution_id", None) or ""
        if not exec_id or exec_id == "None":
            raise ValueError("CDP lineage leg key requires a non-empty execution_id")
        return f"{exec_id}:{step_id}"
    return raw


def _pipeline_leg_within_horizon(leg: Any) -> bool:
    """True until ``max_open_leg_s`` after the leg was admitted."""
    from systems.frontier_consult.cdp_generate_reconcile import max_open_leg_s

    try:
        admitted = datetime.fromisoformat(leg.admitted_at).timestamp()
    except (TypeError, ValueError):
        return True
    open_s = max(0.0, datetime.now(UTC).timestamp() - admitted)
    return open_s < max_open_leg_s(leg.max_wall_s)


def _open_pipeline_leg(leg: Any) -> bool:
    """True when this pipeline leg is polled instead of submitted.

    An in-flight leg is polled until it is abandoned. A proof-emitted leg is
    replayed until its horizon so a run killed after the CDP proof, and before
    the memo write, does not resubmit.
    """
    if (
        leg is None
        or leg.owner != "pipeline"
        or leg.abandoned
        or not leg.satellite_execution_id
    ):
        return False
    if leg.proof_emitted:
        return _pipeline_leg_within_horizon(leg)
    return True


def _proof_snapshot_miss(
    snapshot: dict[str, Any] | None,
    result: CdpGenerateResult | None,
) -> str | None:
    """``not-found`` or ``unknown`` when a proof-emitted poll cannot be replayed.

    A terminal result, including a failure, stays on the poll path. A
    still-running snapshot returns None so the caller does not submit.
    """
    if result is not None:
        return None
    if snapshot is None:
        return "unknown"
    if snapshot.get("error") and "status" not in snapshot:
        code = snapshot.get("status_code")
        err = str(snapshot.get("error") or "").lower()
        if code == 404 or "not found" in err or "not_found" in err:
            return "not-found"
        return "unknown"
    status = str(snapshot.get("status") or "").lower()
    stall = str(snapshot.get("stall_stage") or "").lower()
    if status in ("", "unknown") or stall == "unknown":
        return "unknown"
    return None


def _settle_pipeline_leg(
    leg_key: str,
    result: CdpGenerateResult,
    *,
    already_proof: bool,
) -> None:
    """Close a pipeline leg after a terminal result.

    Success marks proof-emitted. A final failure abandons the leg so the next
    run with the same key submits. ``WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED``
    leaves the leg open, matching ``finalize_cdp_generate``. A leg that is
    already proof-emitted is not abandoned.
    """
    from systems.frontier_consult.cdp_generate_inflight_ledger import (
        mark_abandoned,
        mark_proof_emitted,
    )

    if result.ok:
        mark_proof_emitted(leg_key)
        return
    if already_proof:
        return
    if result.stall_stage == WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED:
        return
    mark_abandoned(leg_key)


def build_cdp_admission_result(
    handler: FrontierDispatchHandler,
    step: StepConfig,
    context: PipelineContext,
    *,
    model: str,
    opts: dict[str, Any],
    role: str | None,
) -> AdmissionResult:
    """Lightweight admission for ``cdp/`` — skips cloud MCP/hydration/tool-set.

    Called by the admission gate for CDP models. Rejects role + cdp combinations,
    resolves user and system prompts (``ValueError`` if both are empty), derives the
    model entity id, and returns an AdmissionResult with MCP/tools disabled,
    ``max_turns=1`` and a publish hook bound to the handler's event bus.
    """
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
    """Run synchronous CDP generate and return dual-bind ``StepOutput``.

    Invoked by FrontierDispatchHandler for ``cdp/`` models. Publishes
    PipelineFrontierDispatchStarted, runs ``run_cdp_generate`` in a worker thread
    (emitting CdpGenerateSubmitted from its callback), then on success emits
    CdpGenerateProof and PipelineFrontierDispatchCompleted. On failure emits
    CdpGenerateStalled and raises ``CdpDispatchError``.

    A re-run whose ``owner='pipeline'`` leg is still open polls that satellite
    once and does not submit again. A proof-emitted leg is replayed the same
    way until its horizon. A final failure abandons the leg so the retry
    submits; an unconfirmed wall-clock abort leaves it open.
    """
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

    from claude_bundles.cdp_model_endpoint import (
        picker_from_model_id,
        result_from_snapshot,
    )

    from systems.frontier_consult.cdp_generate_inflight_ledger import (
        attach_satellite_execution_id,
        read_inflight_leg,
        upsert_inflight_leg,
    )
    from systems.frontier_consult.cdp_generate_reconcile import poll_satellite_snapshot

    leg_key = lineage_leg_key(context, step)
    existing = read_inflight_leg(leg_key)
    loop = asyncio.get_running_loop()
    submitted_sat_id: str | None = None
    started = time.monotonic()

    async def _submit() -> CdpGenerateResult:
        upsert_inflight_leg(
            execution_id=leg_key,
            request_id=request_id,
            thread_id="",
            pointer_turn=1,
            caller_agent=None,
            prompt_uri=f"pipeline://{leg_key}",
            model_id=model,
            max_wall_s=harvest["max_wall_s"],
            owner="pipeline",
        )

        def _on_submitted(satellite_execution_id: str) -> None:
            nonlocal submitted_sat_id
            submitted_sat_id = satellite_execution_id
            attach_satellite_execution_id(
                execution_id=leg_key,
                satellite_execution_id=satellite_execution_id,
            )

            def _publish() -> None:
                publish_cdp_kwargs(
                    CdpGenerateSubmitted,
                    request_id=request_id,
                    execution_id=context.execution_id,
                    satellite_execution_id=satellite_execution_id,
                    model=model,
                )

            loop.call_soon_threadsafe(_publish)

        return await asyncio.to_thread(
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

    already_proof = False
    if _open_pipeline_leg(existing):
        already_proof = bool(existing.proof_emitted)
        if model != existing.model_id:
            logger.warning(
                "cdp lineage leg model mismatch: key=%s leg_model=%s "
                "admission_model=%s",
                leg_key,
                existing.model_id,
                model,
            )
        submitted_sat_id = existing.satellite_execution_id
        snapshot = await poll_satellite_snapshot(existing.satellite_execution_id)
        snap = snapshot if isinstance(snapshot, dict) else None
        result = None
        if snap is not None:
            result = result_from_snapshot(
                snapshot=snap,
                execution_id=context.execution_id,
                satellite_execution_id=existing.satellite_execution_id,
                prompt_uri=existing.prompt_uri,
                picker_model=picker_from_model_id(existing.model_id),
            )
        miss = _proof_snapshot_miss(snap, result) if already_proof else None
        if miss is not None:
            logger.warning(
                "cdp lineage proof leg snapshot %s; submitting: key=%s "
                "satellite_execution_id=%s",
                miss,
                leg_key,
                existing.satellite_execution_id,
            )
            result = await _submit()
            already_proof = False
        elif result is None:
            raise CdpDispatchError(
                f"CDP lineage leg still open: key={leg_key!r} "
                f"satellite_execution_id={existing.satellite_execution_id!r}"
            )
    else:
        result = await _submit()

    _settle_pipeline_leg(leg_key, result, already_proof=already_proof)
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
