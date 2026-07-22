"""Build the background-lead six-block packet for one autonomous charter window.

The autonomous admission mode widens the *within-window* mandate of the default
generate packet (materializer.py) while keeping the window boundary identical:
each window still ends with exactly one CHECKPOINT and stops, and the arc
processes across windows on the next-gated-pickup the charter tick re-admits.

Unlike the one-gated-step generate packet, the autonomous window is authorized to
act as a background lead: decompose the arc into gated G-rows, dispatch sub-legs,
fire R-admit on a *different substrate* (web-anthropic Opus via the ``project_ask``
satellite), restart services for deploy-verify, and run a capped revise loop. The
substrate separation for R-admit is the sole thing that keeps autonomous R honest
(autonomous != self-certify); see ``cortex://notes/system/specs/autonomous-path-sim-charter.md``.
"""

from __future__ import annotations

from universal_logging import get_logger

from .checkpoint_parse import ParsedCheckpoint
from .executor_defaults import DEFAULT_MODEL, DEFAULT_MODEL_KNOBS
from .materializer import _work_summary, handoff_subject, materialize_resume_packet

logger = get_logger(__name__)

REVISE_CAP_DEFAULT = 3

_DENSIFY_FLOOR = (
    "- Use the `agent-bus-discipline` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `orchestrator-workflow` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `path-sim` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `consult-routing` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)"
)


def _scope(window_index: int, root_id: str) -> str:
    knobs = ", ".join(f"{k}={v}" for k, v in sorted(DEFAULT_MODEL_KNOBS.items()))
    return f"""\
<scope>
Goal: Charter-runner AUTONOMOUS window {window_index} — background lead on
agent-bus:{root_id}. Attendance axis = autonomous: run the full path-sim arc
(Q → A+Gate-2 → R-admit → implement+deploy-verify → R-after → close) UNATTENDED
across charter windows. Default executor: {DEFAULT_MODEL} ({knobs}).
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
[R-independence] R-admit MUST be dispatched to web-anthropic Opus 4.8 via the
`project_ask` satellite (jupiter:8770) — a DIFFERENT substrate/family than the
cursor-sdk Grok/Composer running Q/A/implement. Autonomous ≠ self-certify.
Firing your own R is legitimate ONLY because the reviewer model/family differs.
Never collapse R-admit into your own self-assessment.
[restart-auth] service restart for deploy-verify is EXPLICITLY authorized here,
overriding implement-work-item §4B ask-before-restart: quality_gate →
manage(sync_restart) → wait_healthy → live probe. Only the `manage` MCP —
never systemctl / pkill / docker / raw shell kill.
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
{_DENSIFY_FLOOR}
</invariants>"""


def _task_guidance(*, root_id: str, work: str, scoreboard_line: str, revise_cap: int) -> str:
    return f"""\
<task_guidance>
## Resume step 0 (do first)
1. Load agent-bus-discipline (§ Standing root threads + § R12),
   orchestrator-workflow, and path-sim (§ Autonomous charter procession).
2. {scoreboard_line}read the latest CHECKPOINT on agent-bus:{root_id}.

## Autonomous arc (G-row decomposition — lay/advance on the scoreboard)
G1  Q (L0)           cursor-sdk Grok — ranked question table + Question set.
G2  A + Gate-2       cursor-sdk Composer — L1/L2 tables + dense spec
                     (doc_validate gates 6/8/9) + implement_ready assertion.
G3  R-admit          project_ask(op=submit, prompt_uri=cortex://…, converse=true,
                     no_project_uuid=true, model=opus-4.8) → poll to
                     content_proof/archive_uri (long `running` ≠ stalled; ¬ abort
                     on wall-clock) → verdict. Parse with fail-closed gate: only
                     ADMIT/RATIFY → implement; ADMIT_WITH_AMENDMENTS → fold amendments
                     first; RETURN/SCOPE-DRIFT/unparseable → BLOCKED. [R-independence]
G4  implement +      implement the R-admitted bind, then deploy-verify:
    deploy-verify      quality_gate → manage(sync_restart, service=<svc>) →
                       manage(busy_status) wait_healthy → live probe.
                       [restart-auth: manage MCP only]
  G4a/G4b/G4c revise  probe FAILED ⇒ clean CHECKPOINT queues the next revise step
                      (cap={revise_cap}); on exhaustion post BLOCKED (not done).
G5  R-after          /work-item-review · cursor-sdk cursor/grok-4.5 (checkout-native).
G6  close            closeout; friction_close; todo-close with evidence URIs.

## Work for this window
Advance: {work}

Advance exactly the current gated step, using the background-lead capabilities
above as that step requires. Stay inside the gated Next-pickup.

## Acceptance criteria
1. The window's gated step is advanced, revised (clean CHECKPOINT queuing the next
   revise step), or BLOCKED with a clear reason.
2. If this step is R-admit: it was fired via `project_ask` to web-anthropic Opus
   (NOT self-assessed), polled to content_proof/archive_uri, and its verdict is
   recorded with the harvest URI. R-independence guardrail honored.
3. If this step runs deploy-verify: the restart-auth loop (quality_gate →
   manage sync_restart → wait_healthy → live probe) ran via the `manage` MCP only.
   A failed probe queues a revise step (≤{revise_cap}), it does not crash the window.
4. A formal R12 CHECKPOINT is posted on agent-bus:{root_id} (from=cursor-sdk).
   Required sections inline: ## Steps, ## Frictions, ## Sidecars, WIP, Next-pickup,
   Scoreboard URI, RESUME footer.
5. Scoreboard gated lane updated if a G-row status changed.
6. Stop after the CHECKPOINT — no second window.

## Stop conditions (first wins)
CHECKPOINT boundary · revise cap {revise_cap} exhausted (post BLOCKED) ·
judgment-required operator fork · unresolvable failure. NEVER exit with a failure
status on a recoverable probe fail — post a clean revise CHECKPOINT instead.
</task_guidance>"""


def _corpus(root_id: str, scoreboard_uri: str | None) -> str:
    return f"""\
<corpus>
Charter root agent-bus:{root_id}. Scoreboard: {scoreboard_uri or '(see latest CHECKPOINT)'}.
Latest CHECKPOINT on the root is the only state source.
Design: cortex://notes/system/specs/autonomous-path-sim-charter.md.
R-admit transport: docs/tool-reference.md § project_ask (jupiter:8770 satellite).
</corpus>"""


_MCP_CAPABILITIES = """\
<mcp_capabilities>
LIFE/CORTEX MCP: ON — cortex, agent_bus, fs (cortex sandbox).
CODE/VORTEX MCP: ON — workspaces fs, observability, quality_gate, team_dispatch
(fan out Q/A/implement sub-legs), manage (sync_restart authorized for deploy-verify
per [restart-auth]), project_ask (R-admit to web-anthropic Opus per [R-independence]).
</mcp_capabilities>"""


def _output_format(root_id: str) -> str:
    return f"""\
<output_format>
Post the CHECKPOINT on agent-bus:{root_id} with from=cursor-sdk. Include the
CHECKPOINT turn number + scoreboard URI in the worker closeout. On a failed
deploy-verify probe, the CHECKPOINT's gated Next-pickup is the next revise step —
NOT a failure status. Then stop. Agent for friction filing: cursor-sdk.
</output_format>"""


def materialize_autonomous_packet(
    root_id: str,
    parsed: ParsedCheckpoint,
    *,
    scoreboard_uri: str | None = None,
    window_index: int = 1,
    revise_cap: int = REVISE_CAP_DEFAULT,
) -> str:
    """Return a background-lead six-block packet (write to disk before dispatch).

    The packet authorizes the admitted cursor-sdk window to run one gated step of
    the full path-sim arc as a background lead — including firing R-admit on the
    web-anthropic Opus substrate via ``project_ask`` and restarting services for
    deploy-verify — while keeping the one-CHECKPOINT-per-window boundary.
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
    output = _output_format(root_id)
    return f"""\
{scope}
{invariants}
{task}
{corpus}
{_MCP_CAPABILITIES}
{output}
"""


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
) -> tuple[str, str]:
    """Return ``(packet_body, bus_subject)`` for the given admission mode.

    ``autonomous`` yields the background-lead packet; ``generate``/``handoff``
    defer to the unchanged ``materialize_resume_packet`` + ``handoff_subject``.
    """
    if admission_mode == "autonomous":
        packet = materialize_autonomous_packet(
            root_id, parsed, scoreboard_uri=scoreboard_uri, window_index=window_index
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
        root_id, window_index, admission_mode=admission_mode  # type: ignore[arg-type]
    )
