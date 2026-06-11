"""Post-loop observability and ``StepOutput`` assembly.

Emits post-loop anomaly detection (short output, termination shadow) — a
response-fact detection run on both terminal branches, gated internally by
boot-level / provider — and assembles the ``StepOutput`` (raw text, reasoning,
token usage, latency) plus the ``output.json`` payload (content, tool-call
trace, turn/exhaustion metadata, hydration, anomaly hints).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .observability import emit_post_loop_observability
from ..protocol import StepOutput

if TYPE_CHECKING:
    from ..protocol import PipelineContext
    from ..schemas import StepConfig
    from .admission_gate import AdmissionResult
    from .native_loop import LoopOutcome


def build_dispatch_output(
    context: PipelineContext,
    step: StepConfig,
    admission: AdmissionResult,
    outcome: LoopOutcome,
    system: str | None,
) -> StepOutput:
    """Emit post-loop observability and build the dispatch ``StepOutput``.

    ``system`` is the finalized system prompt from the gen-params phase (after
    dispatch-context prepend and any runtime-context block) so the StepOutput
    records exactly what was sent to the provider. Returned anomaly hints are
    threaded through ``StepOutput.json["hints"]`` so the executor can surface
    them in the poll-result payload (see ``executor._extract_output_hints``).
    """
    result = outcome.result
    agent = admission.agent
    model = admission.model
    model_entity_id = admission.model_entity_id

    anomaly_hints = emit_post_loop_observability(
        context=context,
        publish=admission.publish,
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
        latency_ms=outcome.latency_ms,
        model_id=model,
        step_id=step.id,
        system_prompt=system,
        user_prompt=admission.user_prompt,
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
        "finish_reason": outcome.finish_reason,
        "block_reason": outcome.block_reason,
        "exhaustion_summary": outcome.exhaustion_summary,
        "hydration": admission.hydration_meta,
        "hints": anomaly_hints,
        "reasoning": result.reasoning,
        "raw_response": result.raw,
    }
    return output
