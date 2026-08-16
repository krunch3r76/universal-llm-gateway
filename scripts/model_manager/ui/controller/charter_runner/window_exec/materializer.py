"""Build the Resume-step-0 six-block packet for one charter window.

Default executor is cursor-sdk ``cursor/grok-4.6`` (effort=high, fast=false).
Attended handoff mode uses ``from=cursor`` and IDE-open language instead.
The packet encodes Resume step 0, names one unit of work, and binds the stop
contract — post CHECKPOINT on the charter root, then stop (no auto-chain).
"""

from __future__ import annotations

import re
from typing import Literal

from universal_logging import get_logger

from ..checkpoint_schema import (
    ParsedCheckpoint,
    Step,
    append_footer_to_packet,
    first_actionable_step,
    footer_kwargs_for_window,
    output_format_footer_requirement,
)
from ..executor_defaults import DEFAULT_MODEL, DEFAULT_MODEL_KNOBS

logger = get_logger(__name__)

AdmissionMode = Literal["generate", "handoff"]

_LAYER_FLOOR = (
    "- Use the `checkpoint-discipline` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `orchestrator-workflow` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `consult-routing` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `abstraction-layering` skill when this window's step "
    "changes the codebase (canonical slug — seat self-fetches; ¬ fs-read skill "
    "body): enter at the highest still-open layer of architecture → frame → "
    "densify → check → implement; ratification is inherited from the layer "
    "above, so a mechanical leg goes straight to implement and ¬ opens an "
    "R-admit / R-after path-sim window (decision:abstraction-layering)"
)

_IMPLICATION_ARROW_RE = re.compile(
    r"^P\d+\s*(?:⇒|=>)\s*(Steps|Next-pickup|WIP)\s*:\s*(.+)$",
    re.IGNORECASE,
)
_GATED_ROW_RE = re.compile(r"\b[GR]\d+[a-z]?\b")


def _gated_ids_in(text: str) -> list[str]:
    return _GATED_ROW_RE.findall(text)


def _is_t_row_target(text: str) -> bool:
    return bool(re.search(r"\bT\d+[a-z]?\b", text)) and not _GATED_ROW_RE.search(text)


def _resolve_implication_target(
    parsed: ParsedCheckpoint, implication: str
) -> str | None:
    m = _IMPLICATION_ARROW_RE.match(implication.strip())
    if not m:
        return None
    target_kind = m.group(1).strip().lower()
    rest = m.group(2).strip()
    if _is_t_row_target(rest):
        return None
    gated = _gated_ids_in(rest)
    if not gated:
        return None
    want = gated[0]
    if target_kind in {"next-pickup", "steps"}:
        for item in parsed.next_pickup:
            if want in _gated_ids_in(item):
                return item
        for step in parsed.steps:
            if step.status == "done":
                continue
            if want in _gated_ids_in(step.title):
                return f"Step {step.ordinal} — {step.title} (status: {step.status})"
        return None
    for item in parsed.next_pickup:
        if want in _gated_ids_in(item):
            return item
    for step in parsed.steps:
        if step.status == "done":
            continue
        if want in _gated_ids_in(step.title):
            return f"Step {step.ordinal} — {step.title} (status: {step.status})"
    return None


def _first_resolvable_implication(
    parsed: ParsedCheckpoint,
) -> tuple[str | None, bool]:
    if not parsed.implications:
        return None, False
    for line in parsed.implications:
        resolved = _resolve_implication_target(parsed, line)
        if resolved is not None:
            return resolved, False
    return None, True


def _pickup_disambiguates_step(step: Step, parsed: ParsedCheckpoint) -> str | None:
    """Prefer Next-pickup row when it names the same G-row with todo/source identity."""
    if not parsed.next_pickup:
        return None
    step_gated = _gated_ids_in(step.title)
    if not step_gated:
        return None
    for row in parsed.next_pickup:
        row_gated = _gated_ids_in(row)
        if not row_gated or row_gated[0] != step_gated[0]:
            continue
        if parsed.source_ref and parsed.source_ref.lower() in row.lower():
            return row
        if "todo:" in row.lower():
            return row
    return None


def _work_summary(parsed: ParsedCheckpoint) -> str:
    """S1 steering: prefer Implication → gated Step/Next-pickup; else Steps."""
    steered, unresolved = _first_resolvable_implication(parsed)
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
        disambiguated = _pickup_disambiguates_step(step, parsed)
        if disambiguated is not None:
            return disambiguated
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
{_LAYER_FLOOR}
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
{_LAYER_FLOOR}
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
1. Load checkpoint-discipline + agent-bus-discipline § R12 and
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
   - ## Frictions — file each material defect via ``cortex(tool="friction")`` with
     ``charter_root="{root_id}"``, ``window_index=<N>``, ``session_id``, and
     ``actionable`` (``actionable=false`` requires ``actionable_false_reason``).
     Cite each filed id: ``- [filed assertion:<id>] <category>: <one-line note>``.
     Silence only when truly none: ``_None this window._`` — ceremonial Frictions
     (prose-only bullets or fake ids) fail harvest audit.
   - ## Sidecars
   - WIP, Next-pickup, Scoreboard URI, RESUME footer
   - ## What happened (plain) — one short layman paragraph for this window (no gate
     IDs, assertion hashes, or machine tokens).
   Sidecar stubs leave the charter-runner blind — put mandatory sections inline.
3. Scoreboard gated lane updated if a G-row status changed.
4. Stop after the CHECKPOINT — no second window.

## Stop conditions (first wins)
CHECKPOINT boundary · BLOCKED · judgment-required operator fork · unresolvable
failure.
</task_guidance>"""


def _output_format(root_id: str, admission_mode: AdmissionMode, window_index: int) -> str:
    from_agent = "cursor" if admission_mode == "handoff" else "cursor-sdk"
    friction_agent = from_agent
    window_id = f"charter-{root_id}-w{window_index}"
    footer_req = output_format_footer_requirement(window_id=window_id)
    return f"""\
<output_format>
Post the CHECKPOINT on agent-bus:{root_id} with from={from_agent}. Include the
CHECKPOINT turn number + scoreboard URI in the worker closeout. Then stop.
Agent for friction filing: {friction_agent}.
{footer_req}
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
    output = _output_format(root_id, admission_mode, window_index)
    body = f"""\
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
    return append_footer_to_packet(
        body, **footer_kwargs_for_window(root_id, window_index)
    )


def handoff_subject(
    root_id: str, window_index: int, *, admission_mode: AdmissionMode = "generate"
) -> str:
    if admission_mode == "handoff":
        return (
            f"Charter-runner window {window_index} — agent-bus:{root_id} (attended IDE)"
        )
    return (
        f"Charter-runner window {window_index} — agent-bus:{root_id} ({DEFAULT_MODEL})"
    )
