"""Layer-native autonomous packet materializer for ``arc_lane=layer`` codework.

Emits abstraction-layering-shaped six-block packets without path-sim surface
(R-admit absent by design). Closed detent runs the mechanical G5+G6 leg only.
"""

from __future__ import annotations

from universal_logging import get_logger

from ..checkpoint_schema import (
    ParsedCheckpoint,
    append_footer_to_packet,
    footer_kwargs_for_window,
    output_format_footer_requirement,
)
from ..executor_defaults import DEFAULT_MODEL, DEFAULT_MODEL_KNOBS
from .materializer import _work_summary

logger = get_logger(__name__)

REVISE_CAP_DEFAULT = 3

# Layer G3/G4 seat bind — single locus for family-diversity enforcement (6524 R4).
LAYER_G3_SEAT = "cursor/grok-4.5"
LAYER_G4_SEAT = "cursor/gpt-5.6-terra"


def layer_g4_check_family_diverse(
    *,
    g3_seat: str = LAYER_G3_SEAT,
    g4_seat: str = LAYER_G4_SEAT,
) -> bool:
    """True when declared G4 Check seat family differs from G3 Densify seat family."""
    from implement_admission.check_review_substrate import independence_family

    return independence_family(g3_seat) != independence_family(g4_seat)

_LAYER_ARC_FLOOR = (
    "- Use the `checkpoint-discipline` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `orchestrator-workflow` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `abstraction-layering` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `consult-routing` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `docstring-quality` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `event-instrumentation-discipline` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)"
)


def _front_matter(source_ref: str | None) -> str:
    if not source_ref:
        return ""
    return f"---\nsource_ref: {source_ref}\n---\n"


def _scope(window_index: int, root_id: str, *, thin: bool) -> str:
    knobs = ", ".join(f"{k}={v}" for k, v in sorted(DEFAULT_MODEL_KNOBS.items()))
    mode = (
        "CLOSED-DETENT mechanical leg (G5 Implement + G6 Verify)"
        if thin
        else "layer G1–G6 cascade"
    )
    return f"""\
<scope>
Goal: Charter-runner LAYER AUTONOMOUS window {window_index} — {mode} on
agent-bus:{root_id}. Attendance axis = autonomous; arc_lane = layer
(abstraction-layering codework — no downstream ratification windows).
Default executor: {DEFAULT_MODEL} ({knobs}).
</scope>"""


def _invariants(root_id: str, *, thin: bool, revise_cap: int) -> str:
    closed = (
        "[closed-detent] aperture is closed — run the layer mechanical leg "
        "(G5 Implement + G6 Verify) only; escalate by re-opening the highest "
        "open layer (G1/G2/G3) with detent raised — not a path-sim island.\n"
        if thin
        else ""
    )
    return f"""\
<invariants>
[scope] every changed line traces to the gated Next-pickup / Steps item.
[continuity] reconstitute from latest CHECKPOINT + scoreboard only — ¬ linear
thread read.
[layer-arc] codework runs the layering G1–G6 cascade; independence is structural
(upstream cross-family consults + cross-family G4 Check) — not a downstream
ratification window. G5 implement requires proven independence before admit
(layer_independence_unproven blocks at tick admission).
{closed}[background-lead] authorized to dispatch sub-legs, deploy-verify, and
revise within the current gated layer step.
[restart-auth] deploy-verify via manage MCP only when code changed.
[revise-counter] revise cycles tracked on disk (~/.local/share/charter-runner/
revise-count/{{root}}.revise); post BLOCKED when count ≥ {revise_cap}.
[window] end with exactly one CHECKPOINT, then stop.
[checkpoint-contract] worker MUST post R12 CHECKPOINT before closeout.
[executor-lane] declare `executor_lane: implement` on G5 rows with a single
`todo:<slug>`; undeclared layer ordinals fail closed to judgment.
[steps-lane-annotations] Steps carry `[consult:judgment_gap]`, `[judgment]`,
`[implement]`, `[inline]` — never a standing ratification consult token on a
layer arc.
{_LAYER_ARC_FLOOR}
</invariants>"""


def _layer_arc_guidance(*, revise_cap: int) -> str:
    return f"""\
## Layer arc (G-row decomposition — BINDING)
Steps template (machine lane annotations):
1. [ ] G1 — architecture verdict + target shape · [consult:judgment_gap]
2. [ ] G2 — frame (Opus → densifier instructions, ≤120 lines) · [consult:judgment_gap]
3. [ ] G3 — densify dense spec + Gate-2 close · [judgment]
4. [ ] G4 — merged check · [judgment]
5. [ ] G5 — implement (Composer, source_ref) · [implement]
6. [ ] G6 — verify + close (gates · ACs · docstrings) · [inline]

G1  Architecture     consult seat · cdp/fable — architecture verdict sidecar
                     (lane-architecture-consult-brief-template-v2 envelope).
G2  Frame            consult seat · cdp/opus-5 — densifier instructions ≤120L.
G3  Densify          {LAYER_G3_SEAT} — dense spec + Gate-2 + implement_ready.
G4  Check            {LAYER_G4_SEAT} — merged check; refresh spec_sha256.
G5  Implement        cursor/composer-2.5 — contract=implement + source_ref.
G6  Verify + close   inline — quality_gate · files_expected · ACs · docstrings.
                     Escalate to `[consult:judgment_gap]` on highest re-opened
                     layer when predicate fires (never a standing ratification
                     window). Revise cap={revise_cap}."""


def _thin_guidance(work: str) -> str:
    return f"""\
<task_guidance>
Work this window: {work}

## Layer mechanical leg (BINDING — closed detent)
When no gate above is open, run G5 Implement + G6 Verify in one window when
mechanical after bind:
1. Implement the bound patch (cursor/composer-2.5 when mechanical).
2. Deploy-verify if services touched (manage MCP only).
3. G6 verify: quality_gate + files_expected diff + acceptance_criteria +
   docstring-quality scan.
4. Close: friction_close + todo-close with evidence URIs.

Escalation: re-enter the highest open layer as `[consult:judgment_gap]` with
detent raised — do not self-certify a wide bind under closed aperture.
</task_guidance>"""


def _full_guidance(
    *,
    root_id: str,
    work: str,
    scoreboard_line: str,
    revise_cap: int,
) -> str:
    return f"""\
<task_guidance>
## Resume step 0 (do first)
1. Load checkpoint-discipline + orchestrator-workflow + abstraction-layering
   (§ charter tick enrollment annex).
2. {scoreboard_line}read the latest CHECKPOINT on agent-bus:{root_id}.

{_layer_arc_guidance(revise_cap=revise_cap)}

## Work for this window
Advance: {work}

Advance exactly the current gated layer step. Declare `executor_lane: implement`
only on G5 with a resolvable `todo:<slug>` source_ref.

## Acceptance criteria
1. The gated layer step is advanced, revised, or BLOCKED with clear reason.
2. Formal R12 CHECKPOINT posted on agent-bus:{root_id} (from=cursor-sdk).
3. Scoreboard gated lane updated if a G-row status changed.
4. Stop after CHECKPOINT — no second window.
</task_guidance>"""


def _corpus(root_id: str, scoreboard_uri: str | None) -> str:
    return f"""\
<corpus>
Charter root agent-bus:{root_id}. Scoreboard: {scoreboard_uri or "(see latest CHECKPOINT)"}.
Design: abstraction-layering skill + tick enrollment annex.
Architecture consult envelope:
cortex://notes/system/specs/lane-architecture-consult-brief-template-v2.md
</corpus>"""


def _mcp_capabilities() -> str:
    return """\
<mcp_capabilities>
LIFE/CORTEX MCP: ON — cortex, agent_bus, fs (cortex sandbox).
CODE/VORTEX MCP: ON — workspaces fs, observability, quality_gate, team_dispatch,
manage (deploy-verify per [restart-auth]).
</mcp_capabilities>"""


def _output_format(root_id: str, window_index: int) -> str:
    window_id = f"charter-{root_id}-w{window_index}"
    footer_req = output_format_footer_requirement(window_id=window_id)
    return f"""\
<output_format>
Post the CHECKPOINT on agent-bus:{root_id} with from=cursor-sdk. Include the
CHECKPOINT turn number + scoreboard URI in the worker closeout. Then stop.
{footer_req}
</output_format>"""


def materialize_layer_packet(
    root_id: str,
    parsed: ParsedCheckpoint,
    *,
    scoreboard_uri: str | None = None,
    window_index: int = 1,
    revise_cap: int = REVISE_CAP_DEFAULT,
    source_ref: str | None = None,
    thin: bool = False,
) -> str:
    """Return a layer-native six-block packet for ``arc_lane=layer`` autonomous windows."""
    scoreboard_line = (
        f"read the scoreboard gated lane at {scoreboard_uri}, then "
        if scoreboard_uri
        else ""
    )
    work = _work_summary(parsed)
    scope = _scope(window_index, root_id, thin=thin)
    invariants = _invariants(root_id, thin=thin, revise_cap=revise_cap)
    task = (
        _thin_guidance(work)
        if thin
        else _full_guidance(
            root_id=root_id,
            work=work,
            scoreboard_line=scoreboard_line,
            revise_cap=revise_cap,
        )
    )
    body = f"""\
{_front_matter(source_ref)}{scope}
{invariants}
{task}
{_corpus(root_id, scoreboard_uri)}
{_mcp_capabilities()}
{_output_format(root_id, window_index)}
"""
    return append_footer_to_packet(
        body, **footer_kwargs_for_window(root_id, window_index)
    )


def layer_subject(root_id: str, window_index: int, *, thin: bool = False) -> str:
    """Bus subject for a layer autonomous admission."""
    suffix = " (layer closed-detent mechanical leg)" if thin else " (layer autonomous arc)"
    return (
        f"Charter-runner window {window_index} — agent-bus:{root_id}{suffix}"
    )


__all__ = [
    "_LAYER_ARC_FLOOR",
    "LAYER_G3_SEAT",
    "LAYER_G4_SEAT",
    "layer_g4_check_family_diverse",
    "layer_subject",
    "materialize_layer_packet",
]
