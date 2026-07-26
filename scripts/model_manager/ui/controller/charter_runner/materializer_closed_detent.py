"""Closed-detent autonomous packet — thin path-sim, no bundled R arc.

Used when a friction follow-on todo carries ``detent=closed`` (mint triage).
Skill recipe: scope-lock → thin L2 → dissent beat → bind → implement → close.
"""

from __future__ import annotations

from .checkpoint_parse import ParsedCheckpoint
from .executor_defaults import DEFAULT_MODEL, DEFAULT_MODEL_KNOBS
from .materializer import _work_summary

_DENSIFY_FLOOR = (
    "- Use the `agent-bus-discipline` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `path-sim` skill "
    "(§ Closed-detent quick recipe — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `consult-routing` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)"
)


def _front_matter(source_ref: str | None) -> str:
    if not source_ref:
        return ""
    return f"---\nsource_ref: {source_ref}\n---\n"


def _scope(window_index: int, root_id: str) -> str:
    knobs = ", ".join(f"{k}={v}" for k, v in sorted(DEFAULT_MODEL_KNOBS.items()))
    return f"""\
<scope>
Goal: Charter-runner CLOSED-DETENT window {window_index} on agent-bus:{root_id}.
Aperture = closed (|material_sub_parts|≤2, loci known). Run the thin path-sim
recipe UNATTENDED in this window when possible — ¬ full Q→R-admit→R-after arc.
Default executor: {DEFAULT_MODEL} ({knobs}). Selection: detent=closed.
</scope>"""


def _invariants(root_id: str) -> str:
    return f"""\
<invariants>
[scope] every changed line traces to the gated Next-pickup / Steps item.
[continuity] reconstitute from latest CHECKPOINT + scoreboard only — ¬ linear
thread read.
[closed-detent] aperture is closed. Do NOT fire G3 R-admit (cdp/opus-5) or G5
R-after unless the bind touches an invariant or is not self-verifiable — then
escalate: post CONSULT_PENDING + detent=standard|wide and STOP for next tick.
[thin-recipe] Scope-lock (Question/OOS/Good-answer/Origin) → thin L2 (2–3
decorrelated rivals + one-line research-anchor gloss) → one-line dissent beat →
bind (patch loci + falsifier) → implement (Composer ok when mechanical) →
deploy-verify if code landed → friction_close + todo-close.
[R-independence] closed path skips external R by design; escalation restores it.
[restart-auth] deploy-verify via manage MCP only when code changed.
[window] prefer one window to done; if escalate, CHECKPOINT with detent raised.
{_DENSIFY_FLOOR}
</invariants>"""


def _task_guidance(work: str) -> str:
    return f"""\
<task_guidance>
Work this window: {work}

## Closed-detent recipe (BINDING — path-sim skill § Closed-detent quick recipe)
1. Scope-lock four fields for the follow-on todo / friction.
2. Thin L2 fill — 2–3 decorrelated rival binds; one-line research-anchor gloss.
3. One-line dissent beat on the runner-up.
4. Bind — recommended patch loci + falsifier. Prefer the friction's Suggestion
   when it already names files/functions.
5. Implement the bind (cursor/composer-2.5 when mechanical after bind closed).
6. Deploy-verify if services touched; else skip.
7. Close: cortex friction_close(resolution_kind=todo:{{slug}}) + todo
   workflow_state=done + closure sidecar. Cite evidence URIs.

## Escalation (leave closed path)
If a rival bind touches an invariant, loci are not self-verifiable, or
architecture suitability goes live: CHECKPOINT with detent=standard (or wide),
Next-pickup naming the next gated bundled step, then STOP — next tick runs the
full autonomous arc. Do not silently self-certify a wide bind under closed.
</task_guidance>"""


def _output_format(root_id: str) -> str:
    return f"""\
<output_format>
Post exactly one R12 CHECKPOINT on agent-bus:{root_id} (from=cursor-sdk) with
## Steps, ## Frictions, ## Sidecars, WIP, Next-pickup, Scoreboard URI, RESUME,
## What happened (plain). WIP idle = ``_None this window._``.
If done under closed: Next-pickup empty; friction_close cited in Frictions or
Precedents. If escalated: Next-pickup carries detent=standard|wide + gated step.
Then stop — no auto-chain.
</output_format>"""


def materialize_closed_detent_packet(
    root_id: str,
    parsed: ParsedCheckpoint,
    *,
    scoreboard_uri: str | None,
    window_index: int,
    source_ref: str | None = None,
) -> str:
    """Six-block packet for detent=closed friction follow-ons."""
    work = _work_summary(parsed)
    board = scoreboard_uri or "(none — derive from CHECKPOINT)"
    return f"""\
{_front_matter(source_ref)}{_scope(window_index, root_id)}

{_invariants(root_id)}

{_task_guidance(work)}

<corpus>
- Scoreboard: {board}
- Use the `path-sim` skill (§ Closed-detent quick recipe; detent=closed)
- Use the `agent-bus-discipline` skill
- Friction follow-on todo / spawned_by_friction on the gated Next-pickup row
</corpus>

<mcp_capabilities>
cortex, agent_bus, team_dispatch, fs, manage (deploy-verify only), observability
</mcp_capabilities>

{_output_format(root_id)}
"""


def closed_detent_subject(root_id: str, window_index: int) -> str:
    return (
        f"Charter-runner window {window_index} — agent-bus:{root_id} "
        "(autonomous closed-detent)"
    )


__all__ = ["closed_detent_subject", "materialize_closed_detent_packet"]
