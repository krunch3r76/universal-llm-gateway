"""Output formatting for pipeline testing tools.

Covers two audiences:
  - Agent-optimized views (refine-context): progressive detail levels
  - Inspect views: full metadata for debugging timing/tokens
"""

from __future__ import annotations

import json
from typing import Any

from .models import ExecutionSnapshot, ModelCall, StepSnapshot

# ---------------------------------------------------------------------------
# Summary mode — structural overview, zero full text
# ---------------------------------------------------------------------------


def format_summary(step: StepSnapshot) -> str:
    """Structural overview: input names+sizes, prompt sizes, output size, decisions."""
    lines: list[str] = [_step_header(step)]

    lines.append(_format_input_summary(step))

    if step.loop_iterations:
        iterations = step.loop_iterations or []
        max_iter = (step.loop_config or {}).get("max_iterations", "?")
        lines.append(f"Iterations: {len(iterations)}/{max_iter}")
        for it in iterations:
            it_num = it.get("iteration", "?")
            decision = it.get("decision", {})
            action = decision.get("action", "?") if isinstance(decision, dict) else "?"
            reason = decision.get("reason", "") if isinstance(decision, dict) else ""
            reason_preview = reason[:100] + "..." if len(reason) > 100 else reason
            lines.append(f"  {it_num}: {action} — {reason_preview}")

    lines.append(_format_call_summary(step))

    output_size = len(step.raw_output)
    if step.loop_iterations and step.inputs.get("artifact"):
        input_size = len(_stringify(step.inputs["artifact"]))
        delta = output_size - input_size
        lines.append(f"Output: {output_size} chars (delta: {delta:+d} from artifact)")
    else:
        lines.append(f"Output: {output_size} chars")

    return "\n".join(lines)


def _format_input_summary(step: StepSnapshot) -> str:
    if not step.inputs:
        return "Inputs: (none)"
    lines = ["Inputs:"]
    for key, value in step.inputs.items():
        text = _stringify(value)
        detail = _describe_value(value, text)
        lines.append(f"  {key}: {len(text)} chars{detail}")
    return "\n".join(lines)


def _format_call_summary(step: StepSnapshot) -> str:
    if not step.model_calls:
        return "Calls: (none)"
    calls = step.model_calls
    if len(calls) == 1:
        c = calls[0]
        return (
            f"Prompt: system {len(c.system_prompt)} chars, "
            f"user {len(c.user_prompt)} chars"
        )
    lines = [f"Calls: {len(calls)}"]
    for c in calls:
        lines.append(
            f"  [{c.call_label}] {c.model_id}: "
            f"sys {len(c.system_prompt)}, user {len(c.user_prompt)}, "
            f"resp {len(c.response_text)} chars"
        )
    return "\n".join(lines)


def _describe_value(value: Any, text: str) -> str:
    """Short structural description for summary mode."""
    if isinstance(value, list):
        return f" (list, {len(value)} items)"
    if isinstance(value, dict):
        return f" (object, {len(value)} keys)"
    return ""


# ---------------------------------------------------------------------------
# Targeted expansion — one specific piece of content
# ---------------------------------------------------------------------------


def format_input(step: StepSnapshot, key: str) -> str:
    """Show the full value of a single input."""
    if key not in step.inputs:
        available = list(step.inputs.keys())
        return f"Input '{key}' not found. Available: {available}"
    lines = [_step_header(step), ""]
    text = _stringify(step.inputs[key])
    lines.append(f"── Input: {key} ({len(text)} chars) ──")
    lines.append(text)
    return "\n".join(lines)


def format_prompt(step: StepSnapshot, call_label: str | None = None) -> str:
    """Show system + user prompt for a call (no output, no inputs)."""
    call = _select_call(step, call_label)
    if isinstance(call, str):
        return call

    lines = [_step_header(step)]
    label = f" / {call.call_label}" if call.call_label else ""
    lines.append(f"Call: {call.model_id}{label}")
    lines.append("")
    if call.system_prompt:
        lines.append(f"── System Prompt ({len(call.system_prompt)} chars) ──")
        lines.append(call.system_prompt)
        lines.append("")
    lines.append(f"── User Prompt ({len(call.user_prompt)} chars) ──")
    lines.append(call.user_prompt)
    return "\n".join(lines)


def format_output(step: StepSnapshot, call_label: str | None = None) -> str:
    """Show just the output — final step output, or a specific call's response."""
    if call_label:
        call = _select_call(step, call_label)
        if isinstance(call, str):
            return call
        lines = [_step_header(step), ""]
        lines.append(
            f"── Output: {call.call_label} ({len(call.response_text)} chars) ──"
        )
        lines.append(call.response_text)
        return "\n".join(lines)

    lines = [_step_header(step), ""]
    lines.append(f"── Output ({len(step.raw_output)} chars) ──")
    lines.append(step.raw_output)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Full mode — complete step content (existing behavior)
# ---------------------------------------------------------------------------


def format_refine_context(step: StepSnapshot, call_label: str | None = None) -> str:
    """Full step content for prompt refinement.

    Without call_label: shows inputs + iteration trajectory (assess_loop)
    or inputs + prompt + output (regular step).

    With call_label: shows full prompt context for that specific call.
    """
    if call_label:
        return _format_targeted_call(step, call_label)
    if step.loop_iterations:
        return _format_loop_overview(step)
    return _format_single_call(step)


def _format_targeted_call(step: StepSnapshot, call_label: str) -> str:
    call = _find_call(step, call_label)
    if call is None:
        labels = [c.call_label for c in step.model_calls]
        return f"Call '{call_label}' not found. Available: {labels}"

    lines: list[str] = []
    lines.append(f"=== {step.step_name} / {call_label} ({step.step_type}) ===")
    lines.append(f"Model: {call.model_id}")
    lines.append("")
    lines.append(_format_inputs(step))
    lines.append(_format_call_prompts(call))
    return "\n".join(lines)


def _format_single_call(step: StepSnapshot) -> str:
    lines: list[str] = []
    lines.append(f"=== {step.step_name} ({step.step_type}) ===")
    lines.append(f"Model: {step.model_id or '(none)'}")
    lines.append("")
    lines.append(_format_inputs(step))

    if step.model_calls:
        lines.append(_format_call_prompts(step.model_calls[0]))
    elif step.raw_output:
        lines.append("── Output ──")
        lines.append(step.raw_output)

    return "\n".join(lines)


def _format_loop_overview(step: StepSnapshot) -> str:
    iterations = step.loop_iterations or []
    max_iter = (step.loop_config or {}).get("max_iterations", "?")

    lines: list[str] = []
    lines.append(f"=== {step.step_name} ({step.step_type}) ===")
    lines.append(f"Iterations: {len(iterations)}/{max_iter}")
    lines.append("")
    lines.append(_format_inputs(step))

    for it in iterations:
        it_num = it.get("iteration", "?")
        decision = it.get("decision", {})
        action = decision.get("action", "?") if isinstance(decision, dict) else "?"
        lines.append(f"── Iteration {it_num} ──")
        lines.append(_format_decision(decision, action))

        idx = it_num - 1 if isinstance(it_num, int) else ""
        action_calls = [
            c
            for c in step.model_calls
            if c.call_label
            and c.call_label.startswith("action_")
            and c.call_label.endswith(f"_{idx}")
        ]
        for ac in action_calls:
            lines.append(
                f"  {ac.call_label} ({ac.model_id}) → {len(ac.response_text)} chars"
            )
        lines.append("")

    lines.append(f"── Final Output ({len(step.raw_output)} chars) ──")
    lines.append(step.raw_output)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _step_header(step: StepSnapshot) -> str:
    model = step.model_id or "(none)"
    return f"=== {step.step_name} ({step.step_type}) | {model} ==="


def _select_call(step: StepSnapshot, call_label: str | None) -> ModelCall | str:
    """Find a call by label, or return the first. Returns error string on failure."""
    if not step.model_calls:
        return "Step has no model calls"
    if call_label:
        call = _find_call(step, call_label)
        if call is None:
            labels = [c.call_label for c in step.model_calls]
            return f"Call '{call_label}' not found. Available: {labels}"
        return call
    return step.model_calls[0]


def _format_inputs(step: StepSnapshot) -> str:
    if not step.inputs:
        return "── Inputs: (none) ──\n"

    lines = ["── Inputs ──"]
    for key, value in step.inputs.items():
        text = _stringify(value)
        if len(text) > 200:
            lines.append(f"{key} ({len(text)} chars):")
            lines.append(text)
        else:
            lines.append(f"{key}: {text}")
    lines.append("")
    return "\n".join(lines)


def _format_call_prompts(call: ModelCall) -> str:
    lines: list[str] = []
    if call.system_prompt:
        lines.append(f"── System Prompt ({len(call.system_prompt)} chars) ──")
        lines.append(call.system_prompt)
        lines.append("")
    lines.append(f"── User Prompt ({len(call.user_prompt)} chars) ──")
    lines.append(call.user_prompt)
    lines.append("")
    lines.append(f"── Output ({len(call.response_text)} chars) ──")
    lines.append(call.response_text)
    return "\n".join(lines)


def _format_decision(decision: Any, action: str) -> str:
    if not isinstance(decision, dict):
        return f"  → {action}"
    lines = [f"  → {action}"]
    for k, v in decision.items():
        if k == "action":
            continue
        if k == "target":
            continue
        val = _stringify(v)
        if len(val) > 120:
            val = val[:120] + "..."
        lines.append(f"    {k}: {val}")
    return "\n".join(lines)


def _find_call(step: StepSnapshot, call_label: str) -> ModelCall | None:
    for call in step.model_calls:
        if call.call_label == call_label:
            return call
    return None


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list | dict):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return str(value)


# ---------------------------------------------------------------------------
# Inspect views — full metadata for debugging
# ---------------------------------------------------------------------------


def format_inspect_summary(snap: ExecutionSnapshot) -> str:
    """Full pipeline execution summary with timing and token data."""
    lines = [
        f"Pipeline: {snap.pipeline_id}",
        f"Execution: {snap.execution_id}",
        f"Time: {snap.wall_clock}",
        f"Duration: {snap.total_duration_ms:.0f}ms",
        "",
        f"{'Step':<50} {'Type':<18} {'Calls':>5} {'Tokens':>10} {'Duration':>10}",
        "-" * 97,
    ]
    for name in snap.step_order:
        step = snap.steps[name]
        if step.skipped:
            lines.append(f"{name:<50} {'(skipped)':<18} {'':>5} {'':>10} {'':>10}")
            continue
        tokens = f"{step.prompt_tokens}+{step.completion_tokens}"
        dur = f"{step.duration_ms:.0f}ms"
        calls = str(len(step.model_calls))
        lines.append(
            f"{name:<50} {step.step_type:<18} {calls:>5} {tokens:>10} {dur:>10}"
        )
    return "\n".join(lines)


def format_inspect_detail(
    step: StepSnapshot,
    *,
    show_inputs: bool = False,
    show_output: bool = False,
    show_prompts: bool = False,
    call_label: str | None = None,
) -> str:
    """Detailed step view with optional inputs, output, and prompts."""
    lines = [
        f"Step: {step.step_name}",
        f"Type: {step.step_type}",
        f"Model: {step.model_id or '(none)'}",
        f"Duration: {step.duration_ms:.0f}ms",
        f"Tokens: {step.prompt_tokens} prompt + {step.completion_tokens} completion",
        f"Model calls: {len(step.model_calls)}",
    ]

    if step.loop_config:
        lc = step.loop_config
        lines.append("\nAssess Loop Config:")
        lines.append(f"  max_iterations: {lc.get('max_iterations')}")
        lines.append(f"  terminal_action: {lc.get('terminal_action')}")
        if step.loop_iterations:
            for it in step.loop_iterations:
                dec = it.get("decision", {})
                action = dec.get("action", "?") if isinstance(dec, dict) else "?"
                reason = dec.get("reason", "") if isinstance(dec, dict) else ""
                lines.append(
                    f"  iteration {it.get('iteration')}: {action} — {reason[:80]}"
                )

    if step.model_calls:
        lines.append("\nModel Calls:")
        for call in step.model_calls:
            lines.append(
                f"  [{call.call_label or 'default'}] model={call.model_id}, "
                f"tokens={call.prompt_tokens}+{call.completion_tokens}, "
                f"latency={call.latency_ms:.0f}ms"
            )

    if show_inputs:
        lines.append(f"\nInputs ({len(step.inputs)}):")
        for k, v in step.inputs.items():
            source = step.input_sources.get(k, "")
            val_preview = _preview(v, 200)
            lines.append(f"  {k}:")
            lines.append(f"    source: {source}")
            lines.append(f"    value: {val_preview}")

    if show_output:
        lines.append(f"\nRaw Output ({len(step.raw_output)} chars):")
        lines.append(step.raw_output)

    if show_prompts:
        lines.extend(_format_inspect_prompts(step, call_label))

    return "\n".join(lines)


def _format_inspect_prompts(step: StepSnapshot, call_label: str | None) -> list[str]:
    """Format prompt details for inspect view."""
    lines: list[str] = []
    if call_label:
        for c in step.model_calls:
            if c.call_label == call_label:
                lines.extend(_format_inspect_call(c))
                return lines
        labels = [c.call_label for c in step.model_calls]
        lines.append(f"\nCall '{call_label}' not found. Available: {labels}")
        return lines
    for call in step.model_calls:
        lines.extend(_format_inspect_call(call))
    return lines


def _format_inspect_call(call: ModelCall) -> list[str]:
    """Format a single call's prompts and response for inspect view."""
    lines = [f"\n--- [{call.call_label or 'default'}] model={call.model_id} ---"]
    if call.system_prompt:
        lines.append(f"\nSystem Prompt ({len(call.system_prompt)} chars):")
        lines.append(call.system_prompt)
    lines.append(f"\nUser Prompt ({len(call.user_prompt)} chars):")
    lines.append(call.user_prompt)
    lines.append(f"\nResponse ({len(call.response_text)} chars):")
    lines.append(call.response_text)
    return lines


def _preview(value: Any, max_len: int = 200) -> str:
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... ({len(text)} chars)"
