"""Build the background-lead six-block packet for one autonomous charter window.

The autonomous admission mode widens the *within-window* mandate of the default
generate packet (materializer.py) while keeping the window boundary identical:
each window still ends with exactly one CHECKPOINT and stops, and the arc
processes across windows on the next-gated-pickup the charter tick re-admits.

Unlike the one-gated-step generate packet, the autonomous window is authorized to
act as a background lead: decompose the arc into gated G-rows, dispatch sub-legs,
fire R-admit via consult_role: r_admit CONSULT_PENDING (consult seat owns primary
``team_dispatch(model=cdp/opus-5)``; IF6 = CLI; MCP ``project_ask`` removed), restart
services for deploy-verify, and run a capped revise loop. The substrate separation
for R-admit is the sole thing that keeps autonomous R honest (autonomous !=
self-certify); see ``cortex://notes/system/specs/autonomous-path-sim-charter.md``.

``decision:abstraction-layering`` supersedes path-sim ratification windows
for codework, but this packet's G1-G6 prose *is* the path-sim arc and its R
independence is load-bearing for unattended runs — swapping it for the layer
contract is the tick kernel rewrite's job, not a floor edit
(``cortex://notes/system/specs/charter-tick-kernel-rewrite.md``). The attended
generate/handoff floor in ``materializer.py`` already routes codework steps to the
``abstraction-layering`` skill.
"""

from __future__ import annotations

from universal_logging import get_logger

from ..checkpoint_schema import (
    ParsedCheckpoint,
    append_footer_to_packet,
    footer_kwargs_for_window,
    output_format_footer_requirement,
)
from ..executor_defaults import JUDGMENT_MODEL, JUDGMENT_MODEL_KNOBS
from .materializer import _work_summary, handoff_subject, materialize_resume_packet
from .materializer_autonomous_arc import autonomous_arc_guidance
from .materializer_closed_detent import (
    closed_detent_subject,
    materialize_closed_detent_packet,
)
from .materializer_consult import (
    _open_layer_consult_gate,
    consult_subject_for_arc,
    materialize_consult_packet,
)
from .materializer_layer import layer_subject, materialize_layer_packet
from .materializer_operator_proxy import (
    materialize_operator_proxy_packet,
    operator_proxy_subject,
)

logger = get_logger(__name__)

REVISE_CAP_DEFAULT = 3

_DENSIFY_FLOOR = (
    "- Use the `checkpoint-discipline` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `orchestrator-workflow` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `path-sim` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `consult-routing` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)"
)


def _scope(window_index: int, root_id: str) -> str:
    knobs = ", ".join(f"{k}={v}" for k, v in sorted(JUDGMENT_MODEL_KNOBS.items()))
    return f"""\
<scope>
Goal: Charter-runner AUTONOMOUS window {window_index} — background lead on
agent-bus:{root_id}. Attendance axis = autonomous: run the full path-sim arc
(Q → A+Gate-2 → R-admit → implement+deploy-verify → R-after → close) UNATTENDED
across charter windows. Default executor: {JUDGMENT_MODEL} ({knobs}).
Selection mode: autonomous procession (todo attr attendance=autonomous).
</scope>"""


def _invariants(root_id: str, revise_cap: int) -> str:
    return f"""\
<invariants>
[scope] every changed line traces to the gated Next-pickup / Steps item.
[continuity] reconstitute from latest CHECKPOINT + scoreboard only — ¬ linear
thread read.
[background-lead] this window is authorized to act as a background lead:
decompose the arc into gated G-rows, dispatch sub-legs (team_dispatch Q/A/
implement), fire satellite R-admit, restart services for deploy-verify, and
revise — NOT the one-gated-step-then-stop generate default.
[R-independence] R-admit MUST be hosted on a consult seat with consult_role: r_admit
— the autonomous holder posts CONSULT_PENDING at the end of G2 and STOPs; it must NOT fire
R-admit transport from this window. The consult seat owns primary submit→poll→E2
via team_dispatch(model=cdp/opus-5) (web-anthropic Opus). IF6 = CLI
project-ask (MCP project_ask removed). Autonomous ≠ self-certify. Never collapse R-admit into your own
self-assessment. External review for this arc is the G3 cdp/opus-5 R-admit. Do NOT open
implement-todo §3b Gate-6, and do NOT dispatch role=reviewer or role=skeptic —
check_requested is not set on charter work items.
[sealed-unattended] When pinning the R prompt URI at G3, the prompt body MUST
include the sealed unattended clause (a:26156): answer with best judgment;
state assumptions; ¬ clarifying questions; ¬ wait for a human. Cowork Qs
false-complete sealed harvests — ¬ expect charter auto-reply.
[IF6-escape] holder-fired R-admit (CLI ``claude-ai-sync-jupiter project-ask``)
remains the dual-host emergency path; MCP project_ask is removed.
[restart-auth] service restart for deploy-verify is EXPLICITLY authorized here,
overriding implement-work-item §4B ask-before-restart: quality_gate →
manage(sync_restart) → wait_healthy → live probe. Only the `manage` MCP —
never systemctl / pkill / docker / raw shell kill. When the worker does not
run deploy-verify, charter harvest propagation executes propagation_residue
sync_restart actions after the window closes (blocking drain for GIW).
[R-verdict-gate] after R-admit harvest, parse the merits verdict with the
fail-closed gate: only ADMIT or RATIFY may advance to implement; ADMIT_WITH_AMENDMENTS
requires amendments folded + dense spec re-validated before implement; RETURN,
SCOPE-DRIFT, or unparseable ⇒ post BLOCKED (never advance — autonomous ≠ self-certify).
[revise-counter] the charter runner tracks revise cycles on disk
(~/.local/share/charter-runner/revise-count/{{root}}.revise); read the count before
admitting a revise pickup and post BLOCKED when count ≥ {revise_cap}.
[probe-vs-crash] probe FAIL ⇒ clean success-shaped CHECKPOINT queuing the next revise
step (increment revise counter). Worker crash/timeout ⇒ STOP root via worker_failed —
never mask a crash as a probe revise.
[window] end this window with exactly one CHECKPOINT, then stop. The next tick
admits the next gated step. Do NOT run an immortal loop inside one window.
[checkpoint-contract] worker closeout ``status=complete`` without a root
CHECKPOINT after the WIP admission pointer is a contract breach
(``checkpoint_missing``). Nothing repairs it — the runner never authors a
CHECKPOINT for you (Phase 3 retired self-heal), so the window is lost and this
root's pickup stalls until a human reseeds the tip. You MUST post the R12
CHECKPOINT yourself before closeout.
[consult-boundary] when judgment/ambitious work needs external consult, post
CONSULT_PENDING with consult_role: judgment_gap (pin Question/OOS + corpus manifest)
or consult_role: r_admit at G3 (pin R prompt URI) and STOP — never nested
team_dispatch/cursor-sdk consult under this autonomous holder (depth-1 only;
next tick admits a separate cross-family consult seat).
[consult-depth] consult seats are single-round (depth-1); they cannot dispatch
further consults. Resume worker windows only after consult provenance is on the
root CHECKPOINT / todo attrs.
[closeout-next-pickup] Closeout / R-after / arc-close CHECKPOINT Next-pickup MUST
include a gated token (`G\\d+`/`R\\d+` or allowlisted CLOSEOUT/arc-close). Canonical:
`G6 — R-after …`. Bare `R-after` alone ⇒ no_gated_pickup → state-close (a:26092).
[executor-lane] a Next-pickup row MAY declare `executor_lane: implement` to route
the next window to the mechanical Composer implement bind, or `executor_lane:
judgment` to hold the Grok bind. Declare `implement` ONLY for a G4-proper code
edit whose work item is already implement-ready — the row (or Anchor) must name a
single `todo:<slug>`, else the machine fails closed to judgment. Revise rows
(G4a/G4b/G4c) stay judgment: probe-fail windows post a CHECKPOINT and no file
change, which `contract=implement` would label degraded. Undeclared ⇒ G-ordinal
heuristic, and anything ambiguous ⇒ judgment.
[steps-lane-annotations] Steps titles MUST carry machine lane tokens
(`[consult:r_admit]`, `[consult:judgment_gap]`, `[implement]`, `[inline]`,
`[judgment]`). The charter tick classifier owns window_kind from the first
open annotated row — CONSULT_PENDING on Next-pickup is a cross-check only.
A G4 implement window MUST NOT mark a Steps row annotated `[consult:*]` as done.
[stale-r-corpus-sha] On CONSULT_PENDING + consult_role: r_admit, Sidecars MUST pin
the live dense-spec hash on the **same row** as the dense-spec URI
(`Dense spec: cortex://… · spec_sha256:<64-hex>`). Machine pre-fire refuses
mismatch/missing/ambiguous/malformed/malformed_uri/unreadable (reason=stale_r_corpus_sha).
Refresh = holder re-fs.read → rewrite Sidecars same-row hash → re-CHECKPOINT;
¬ consult-seat auto-rewrite (a:26095).
{_DENSIFY_FLOOR}
</invariants>"""


def _task_guidance(
    *, root_id: str, work: str, scoreboard_line: str, revise_cap: int
) -> str:
    return f"""\
<task_guidance>
## Resume step 0 (do first)
1. Load checkpoint-discipline + agent-bus-discipline § R12,
   orchestrator-workflow, and path-sim (§ Autonomous charter procession).
2. {scoreboard_line}read the latest CHECKPOINT on agent-bus:{root_id}.

{autonomous_arc_guidance(revise_cap=revise_cap)}

## Work for this window
Advance: {work}

Advance exactly the current gated step, using the background-lead capabilities
above as that step requires. Stay inside the gated Next-pickup.

## Acceptance criteria
1. The window's gated step is advanced, revised (clean CHECKPOINT queuing the next
   revise step), or BLOCKED with a clear reason.
2. If this step is G2: the window ENDS with CONSULT_PENDING + consult_role: r_admit,
   R prompt URI pinned, and the dense-spec `spec_sha256:<64-hex>` on the SAME
   Sidecars row as the dense-spec URI. Do NOT self-fire cdp/ or project_ask from
   this holder window — the next tick admits the consult seat.
3. If this step runs deploy-verify: the restart-auth loop (quality_gate →
   manage sync_restart → wait_healthy → live probe) ran via the `manage` MCP only.
   A failed probe queues a revise step (≤{revise_cap}), it does not crash the window.
4. If this step is G4: run the G5 mechanical R-after inline (quality_gate +
   files_expected diff + acceptance_criteria + docstring scan). Escalate to a second
   CONSULT_PENDING + consult_role: r_admit only under the G5 escalation predicate;
   otherwise proceed straight to G6 close. Do NOT queue a standalone G5 window.
5. A formal R12 CHECKPOINT is posted on agent-bus:{root_id} (from=cursor-sdk).
   **Subject MUST start with ``CHECKPOINT``** (e.g. ``CHECKPOINT wave N — …``) even
   when the stop class is CONSULT_PENDING — the tick tip-classifier keys on subject
   prefix; put ``CONSULT_PENDING`` in the body + Next-pickup, not as the subject verb.
   Required sections inline: ## Steps, ## Frictions, ## Sidecars, WIP, Next-pickup,
   Scoreboard URI, RESUME footer, ## What happened (plain) (layman window summary —
   no gate IDs or assertion hashes). **Steps lane tokens (BINDING):** each G-row
   title carries `[consult:r_admit]`, `[consult:judgment_gap]`, `[implement]`,
   `[inline]`, or `[judgment]` — e.g. `3. [ ] G3 — R-admit · [consult:r_admit]`.
   Post CONSULT_PENDING on Next-pickup as cross-check when stopping for consult.
   **WIP body (BINDING):** under
   ``## WIP / In-flight`` write exactly ``_None this window._`` or bare ``none``
   when idle — the tick parser treats FOL prose ``WIP=none`` as eligible now, but
   prefer the silence marker. Do NOT invent freeform WIP tokens
   (``WIP=holder…``, multi-line active prose without a real holder).
   ``## Frictions`` MUST file via
   ``cortex(tool="friction")`` with ``charter_root="{root_id}"``, ``window_index``,
   ``session_id``, and ``actionable``; cite ``[filed assertion:<id>]`` per row or
   ``_None this window._`` when truly none — prose-only bullets fail harvest audit.
   ``status=complete`` without this CHECKPOINT is
   ``checkpoint_missing`` — unrecoverable; nothing re-queues the window.
6. Scoreboard gated lane updated if a G-row status changed.
7. Stop after the CHECKPOINT — no second window.

## Stop conditions (first wins)
CHECKPOINT boundary · CONSULT_PENDING (external consult — stop; no nested SDK) ·
revise cap {revise_cap} exhausted (post BLOCKED) ·
judgment-required operator fork · unresolvable failure. NEVER exit with a failure
status on a recoverable probe fail — post a clean revise CHECKPOINT instead.
</task_guidance>"""


def _corpus(root_id: str, scoreboard_uri: str | None) -> str:
    return f"""\
<corpus>
Charter root agent-bus:{root_id}. Scoreboard: {scoreboard_uri or "(see latest CHECKPOINT)"}.
Latest CHECKPOINT on the root is the only state source.
Design: cortex://notes/system/specs/autonomous-path-sim-charter.md.
R-admit transport: primary team_dispatch(model=cdp/opus-5); IF6 = CLI
(docs/tool-reference.md § cse_session / cdp model-endpoint). MCP project_ask removed.
</corpus>"""


_MCP_CAPABILITIES = """\
<mcp_capabilities>
LIFE/CORTEX MCP: ON — cortex, agent_bus, fs (cortex sandbox).
CODE/VORTEX MCP: ON — workspaces fs, observability, quality_gate, team_dispatch
(fan out Q/A/implement sub-legs), manage (sync_restart authorized for deploy-verify
per [restart-auth]). R-admit is consult-hosted — holder does NOT fire cdp/ or
project_ask at G3 (consult seat owns primary cdp/; IF6 = CLI).
</mcp_capabilities>"""


def _output_format(root_id: str, window_index: int) -> str:
    window_id = f"charter-{root_id}-w{window_index}"
    footer_req = output_format_footer_requirement(window_id=window_id)
    return f"""\
<output_format>
Post the CHECKPOINT on agent-bus:{root_id} with from=cursor-sdk. Include the
CHECKPOINT turn number + scoreboard URI in the worker closeout. On a failed
deploy-verify probe, the CHECKPOINT's gated Next-pickup is the next revise step —
NOT a failure status. Then stop. Agent for friction filing: cursor-sdk.
{footer_req}
</output_format>"""


def _front_matter(source_ref: str | None) -> str:
    """YAML front matter carrying ``source_ref`` for the implement gate.

    ``generate_wrap.prepare_implement_packet`` resolves the readiness gate's
    ``source_ref`` from packet front matter and **discards** the body value, and
    ``frontmatter_value`` returns ``None`` when there is no ``---`` region at
    all — at which point ``require_implement_ready`` short-circuits and the
    window runs with no triage check, no implement-ready assertion, and no
    ``spec_sha256``. An implement-lane packet must therefore always be stamped;
    ``executor_routing`` refuses the implement lane when no ref resolves.
    """
    if not source_ref:
        return ""
    return f"---\nsource_ref: {source_ref}\n---\n"


def materialize_autonomous_packet(
    root_id: str,
    parsed: ParsedCheckpoint,
    *,
    scoreboard_uri: str | None = None,
    window_index: int = 1,
    revise_cap: int = REVISE_CAP_DEFAULT,
    source_ref: str | None = None,
) -> str:
    """Return a background-lead six-block packet (write to disk before dispatch).

    The packet authorizes the admitted cursor-sdk window to run one gated step of
    the full path-sim arc as a background lead — including firing R-admit on the
    web-anthropic Opus substrate via ``team_dispatch(model=cdp/…)`` and restarting services for
    deploy-verify — while keeping the one-CHECKPOINT-per-window boundary.

    ``source_ref`` stamps front matter for an implement-lane window.
    """
    scoreboard_line = (
        f"read the scoreboard gated lane at {scoreboard_uri}, then "
        if scoreboard_uri
        else ""
    )
    work = _work_summary(parsed)
    scope = _scope(window_index, root_id)
    invariants = _invariants(root_id, revise_cap)
    task = _task_guidance(
        root_id=root_id,
        work=work,
        scoreboard_line=scoreboard_line,
        revise_cap=revise_cap,
    )
    corpus = _corpus(root_id, scoreboard_uri)
    output = _output_format(root_id, window_index)
    body = f"""\
{_front_matter(source_ref)}{scope}
{invariants}
{task}
{corpus}
{_MCP_CAPABILITIES}
{output}
"""
    return append_footer_to_packet(
        body, **footer_kwargs_for_window(root_id, window_index)
    )


def autonomous_subject(root_id: str, window_index: int) -> str:
    """Return the bus subject line for an autonomous background-lead admission.

    Distinct from the generate/handoff subjects so the transcript and any operator
    scanning the root thread can tell an autonomous window apart at a glance.
    """
    return (
        f"Charter-runner window {window_index} — agent-bus:{root_id} "
        "(autonomous background lead)"
    )


def select_packet(
    root_id: str,
    parsed: ParsedCheckpoint,
    *,
    scoreboard_uri: str | None,
    window_index: int,
    admission_mode: str,
    consult_role: str | None = None,
    source_ref: str | None = None,
    arc_lane: str = "layer",
) -> tuple[str, str]:
    """Return ``(packet_body, bus_subject)`` for the given admission mode.

    ``autonomous`` yields the background-lead packet; when Next-pickup declares
    ``detent=closed`` (friction conveyor triage), yields the thin closed-detent
    packet instead of the full Q→R arc (path_sim) or the layer mechanical leg
    (layer). ``consult`` yields the depth-1 consult seat packet; ``generate``/
    ``handoff`` defer to ``materialize_resume_packet``.
    """
    from ..checkpoint_schema import pickup_detent

    if admission_mode == "consult":
        packet = materialize_consult_packet(
            root_id,
            parsed,
            scoreboard_uri=scoreboard_uri,
            window_index=window_index,
            arc_lane=arc_lane,
        )
        role = consult_role or parsed.consult_role
        gate_id = _open_layer_consult_gate(parsed) if arc_lane == "layer" else None
        return packet, consult_subject_for_arc(
            root_id,
            window_index,
            consult_role=role,
            arc_lane=arc_lane,
            gate_id=gate_id,
        )
    if admission_mode == "operator_proxy":
        packet = materialize_operator_proxy_packet(
            root_id, parsed, scoreboard_uri=scoreboard_uri, window_index=window_index
        )
        return packet, operator_proxy_subject(root_id, window_index)
    if admission_mode == "autonomous":
        if arc_lane == "layer":
            thin = pickup_detent(parsed) == "closed"
            packet = materialize_layer_packet(
                root_id,
                parsed,
                scoreboard_uri=scoreboard_uri,
                window_index=window_index,
                source_ref=source_ref,
                thin=thin,
            )
            return packet, layer_subject(root_id, window_index, thin=thin)
        if pickup_detent(parsed) == "closed":
            packet = materialize_closed_detent_packet(
                root_id,
                parsed,
                scoreboard_uri=scoreboard_uri,
                window_index=window_index,
                source_ref=source_ref,
            )
            return packet, closed_detent_subject(root_id, window_index)
        packet = materialize_autonomous_packet(
            root_id,
            parsed,
            scoreboard_uri=scoreboard_uri,
            window_index=window_index,
            source_ref=source_ref,
        )
        return packet, autonomous_subject(root_id, window_index)
    packet = materialize_resume_packet(
        root_id,
        parsed,
        scoreboard_uri=scoreboard_uri,
        window_index=window_index,
        admission_mode=admission_mode,  # type: ignore[arg-type]
    )
    return packet, handoff_subject(
        root_id,
        window_index,
        admission_mode=admission_mode,  # type: ignore[arg-type]
    )
