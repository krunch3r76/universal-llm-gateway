"""Build the Resume-step-0 six-block packet for one charter window.

Default executor is cursor-sdk ``cursor/grok-4.5`` (effort=high, fast=false).
Attended handoff mode uses ``from=cursor`` and IDE-open language instead.
The packet encodes Resume step 0, names one unit of work, and binds the stop
contract — post CHECKPOINT on the charter root, then stop (no auto-chain).
"""

from __future__ import annotations

from typing import Literal

from universal_logging import get_logger

from .checkpoint_parse import (
    ParsedCheckpoint,
    first_actionable_step,
    first_resolvable_implication,
)
from .executor_defaults import DEFAULT_MODEL, DEFAULT_MODEL_KNOBS

logger = get_logger(__name__)

AdmissionMode = Literal["generate", "handoff"]

_DENSIFY_FLOOR = (
    "- Use the `agent-bus-discipline` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `orchestrator-workflow` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `consult-routing` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)"
)


def _work_summary(parsed: ParsedCheckpoint) -> str:
    """S1 steering: prefer Implication → gated Step/Next-pickup; else Steps."""
    steered, unresolved = first_resolvable_implication(parsed)
    if unresolved:
        logger.warning(
            "implication_target_unresolved root_implications=%s next_pickup=%s",
            parsed.implications,
            parsed.next_pickup,
        )
    if steered is not None:
        return steered
    step = first_actionable_step(parsed)
    if step is not None:
        return f"Step {step.ordinal} — {step.title} (status: {step.status})"
    if parsed.next_pickup:
        return "; ".join(parsed.next_pickup[:3])
    return "the first gated Next-pickup item on the scoreboard"


def _generate_scope(window_index: int, root_id: str) -> str:
    knobs = ", ".join(f"{k}={v}" for k, v in sorted(DEFAULT_MODEL_KNOBS.items()))
    return f"""\
<scope>
Goal: Charter-runner window {window_index} — one continuity slice on
agent-bus:{root_id}. Default executor: {DEFAULT_MODEL} ({knobs}).
Selection mode: targeted.
</scope>"""


def _handoff_scope(window_index: int, root_id: str) -> str:
    return f"""\
<scope>
Goal: Charter-runner window {window_index} — one continuity slice on
agent-bus:{root_id}. Attended substrate: open Multitask/IDE on the worker
thread and execute as Composer (from=cursor). Selection mode: targeted.
</scope>"""


def _generate_invariants(root_id: str) -> str:
    return f"""\
<invariants>
[scope] every changed line traces to the gated Next-pickup / Steps item.
[continuity] reconstitutes from latest CHECKPOINT + scoreboard only — ¬ linear
thread read.
[window] exactly one window; do not auto-chain a second window.
[executor] default seat is cursor-sdk / {DEFAULT_MODEL}; do not silently switch
models. Opus-class code review is a separate CDP step — not this default window.
{_DENSIFY_FLOOR}
</invariants>"""


def _handoff_invariants() -> str:
    return f"""\
<invariants>
[scope] every changed line traces to the gated Next-pickup / Steps item.
[continuity] reconstitutes from latest CHECKPOINT + scoreboard only — ¬ linear
thread read.
[window] exactly one window; do not auto-chain a second window.
[executor] attended Composer on IDE worker thread (from=cursor); operator opens
the thread — do not silently switch to unattended generate.
{_DENSIFY_FLOOR}
</invariants>"""


def _task_guidance(
    *,
    root_id: str,
    work: str,
    scoreboard_line: str,
    admission_mode: AdmissionMode,
) -> str:
    from_agent = "cursor" if admission_mode == "handoff" else "cursor-sdk"
    return f"""\
<task_guidance>
## Resume step 0 (do first)
1. Load agent-bus-discipline (§ Standing root threads + § R12) and
   orchestrator-workflow for coding arcs.
2. {scoreboard_line}read the latest CHECKPOINT on agent-bus:{root_id}.

## Work for this window
Advance: {work}

Stay inside the gated Next-pickup. Do not promote tangents without an operator
bind or registered child thread.

## Acceptance criteria
1. The window's gated step is advanced or blocked with a clear reason.
2. A formal R12 CHECKPOINT is posted on agent-bus:{root_id} (from={from_agent}).
   Required sections (inline on the bus turn body):
   - ## Steps
   - ## Frictions (file each via cortex friction)
   - ## Sidecars
   - WIP, Next-pickup, Scoreboard URI, RESUME footer
   Sidecar stubs leave the charter-runner blind — put mandatory sections inline.
3. Scoreboard gated lane updated if a G-row status changed.
4. Stop after the CHECKPOINT — no second window.

## Stop conditions (first wins)
CHECKPOINT boundary · BLOCKED · judgment-required operator fork · unresolvable
failure.
</task_guidance>"""


def _output_format(root_id: str, admission_mode: AdmissionMode) -> str:
    from_agent = "cursor" if admission_mode == "handoff" else "cursor-sdk"
    friction_agent = from_agent
    return f"""\
<output_format>
Post the CHECKPOINT on agent-bus:{root_id} with from={from_agent}. Include the
CHECKPOINT turn number + scoreboard URI in the worker closeout. Then stop.
Agent for friction filing: {friction_agent}.
</output_format>"""


def materialize_resume_packet(
    root_id: str,
    parsed: ParsedCheckpoint,
    *,
    scoreboard_uri: str | None = None,
    window_index: int = 1,
    admission_mode: AdmissionMode = "generate",
) -> str:
    """Return a six-block handoff packet body (write to disk before handoff)."""
    scoreboard_line = (
        f"read the scoreboard gated lane at {scoreboard_uri}, then "
        if scoreboard_uri
        else ""
    )
    work = _work_summary(parsed)
    corpus = (
        f"Charter root agent-bus:{root_id}. "
        f"Scoreboard: {scoreboard_uri or '(see latest CHECKPOINT)'}. "
        "Latest CHECKPOINT on the root is the only state source."
    )
    scope = (
        _handoff_scope(window_index, root_id)
        if admission_mode == "handoff"
        else _generate_scope(window_index, root_id)
    )
    invariants = (
        _handoff_invariants()
        if admission_mode == "handoff"
        else _generate_invariants(root_id)
    )
    task = _task_guidance(
        root_id=root_id,
        work=work,
        scoreboard_line=scoreboard_line,
        admission_mode=admission_mode,
    )
    output = _output_format(root_id, admission_mode)
    return f"""\
{scope}
{invariants}
{task}
<corpus>
{corpus}
</corpus>
<mcp_capabilities>
LIFE/CORTEX MCP: ON — cortex, agent_bus, fs (cortex sandbox).
CODE/VORTEX MCP: ON — workspaces fs, observability as needed for verify.
</mcp_capabilities>
{output}
"""


def handoff_subject(
    root_id: str, window_index: int, *, admission_mode: AdmissionMode = "generate"
) -> str:
    if admission_mode == "handoff":
        return (
            f"Charter-runner window {window_index} — agent-bus:{root_id} "
            "(attended IDE)"
        )
    return (
        f"Charter-runner window {window_index} — agent-bus:{root_id} "
        f"({DEFAULT_MODEL})"
    )
