"""Build the depth-1 consult-seat six-block packet for one charter window.

When the latest CHECKPOINT stop is ``CONSULT_PENDING``, the tick admits a consult
seat — dual-role under the same ``window_kind=consult`` host wire
(``consult_host_generate_body`` → cursor-sdk generate ``read_only``):

- ``consult_role: judgment_gap`` → host fires ``team_dispatch(model=cdp/opus-5)``
  for the path-sim judgment consult (auto-wake; no ``push_reminder`` handoff).
- ``consult_role: r_admit`` → host fires ``team_dispatch(model=cdp/opus-5)`` for
  R-admit; MCP ``project_ask`` = escape only.

Consult seats must not dispatch nested consults (depth-1 only).
"""

from __future__ import annotations

import re

from ..checkpoint_schema import (
    ParsedCheckpoint,
    append_footer_to_packet,
    footer_kwargs_for_window,
    output_format_footer_requirement,
)
from ..residue_fingerprint import normalize_next_pickup

ConsultRole = str  # r_admit | judgment_gap

_CONSULT_PENDING_RE = re.compile(r"\bCONSULT_PENDING\b", re.I)


class LayerConsultGateUnresolvedError(Exception):
    """Layer consult materialization refused — no classifiable G1/G2 stop."""


def _is_r_admit(parsed: ParsedCheckpoint) -> bool:
    return parsed.consult_role == "r_admit"


def _scope_layer_judgment(window_index: int, root_id: str, *, gate_id: str) -> str:
    if gate_id == "G1":
        seat = "cdp/fable"
        role = "Architecture — verdict sidecar (R1 envelope)"
    else:
        seat = "cdp/opus-5"
        role = "Frame — densifier instructions ≤120 lines"
    return f"""\
<scope>
Goal: Charter-runner LAYER CONSULT window {window_index} — {role} on
agent-bus:{root_id}. The holder posted CONSULT_PENDING with
consult_role: judgment_gap; this window owns primary consult via
team_dispatch(model={seat}) submit→poll_hint→provenance (depth-1 only).
MCP project_ask = escape.
</scope>"""


def _invariants_layer_judgment(root_id: str, *, gate_id: str) -> str:
    envelope = (
        "cortex://notes/system/specs/lane-architecture-consult-brief-template-v2.md"
        if gate_id == "G1"
        else "densifier instructions ≤120 lines (not a dense spec)"
    )
    seat = "cdp/fable" if gate_id == "G1" else "cdp/opus-5"
    return f"""\
<invariants>
[consult-boundary] CONSULT_PENDING + consult_role: judgment_gap on
agent-bus:{root_id} — scope-lock fields are pinned on the root CHECKPOINT.
[layer-ask] the consult ask is layering-shaped: pin the architecture exit
contract ({envelope}) — not question-table layer-search prose.
[consult-independence] fire via team_dispatch(model={seat},
contract=light-bounded) to web-anthropic — a DIFFERENT substrate/family than
this cursor-sdk seat. Never self-answer the judgment gap.
[sealed-unattended] CDP prompt MUST include the sealed unattended clause (a:26156).
[depth-1] harvest one consult reply; write shared provenance fields.
[window] end with exactly one CHECKPOINT on the root, then stop.
- Use the `consult-routing` skill (canonical slug — seat self-fetches)
- Use the `checkpoint-discipline` skill (canonical slug — seat self-fetches)
- Use the `abstraction-layering` skill (canonical slug — seat self-fetches)
</invariants>"""


def _task_guidance_layer_judgment(
    *,
    root_id: str,
    scoreboard_line: str,
    identity_block: str,
    gate_id: str,
) -> str:
    seat = "cdp/fable" if gate_id == "G1" else "cdp/opus-5"
    ask = (
        "architecture verdict sidecar per lane-architecture-consult-brief-template-v2 "
        "(contract-envelope-v0 §8 Falsifiers)"
        if gate_id == "G1"
        else "densifier instructions ≤120 lines — not a dense spec"
    )
    return f"""\
<task_guidance>
## Resume step 0 (do first)
1. Load consult-routing + checkpoint-discipline + abstraction-layering.
2. {scoreboard_line}read the latest CHECKPOINT on agent-bus:{root_id} — confirm
   CONSULT_PENDING + consult_role: judgment_gap.

{identity_block}
## Layer {gate_id} consult work (this seat owns submit→poll→provenance)
- Ask shape: {ask}
- Primary: team_dispatch(op=generate, model={seat}, contract=light-bounded, …)
- Record shared provenance (consult_thread, verdict, consultant_family,
  consultant_substrate) on the root CHECKPOINT.
- G1 exit duty: stamp architecture ``document:`` + ``derived_from`` edge when
  architecture verdict closes (abstraction-layering § Stage 0 attach).

## Stop conditions
CHECKPOINT boundary · unresolvable cdp/ transport · IF6 tripwire ⇒ BLOCKED.
</task_guidance>"""


def _open_layer_consult_gate(parsed: ParsedCheckpoint) -> str | None:
    """Return G1 or G2 for the first open layer consult stop, else None."""
    from ..gate_lane_classifier import classify, parse_gate_rows

    rows = parse_gate_rows(parsed.steps)
    req = classify(rows)
    if req is not None and req.kind == "consult" and req.gate_id in {"G1", "G2"}:
        return req.gate_id
    for row in parsed.next_pickup:
        upper = row.upper()
        if _CONSULT_PENDING_RE.search(row):
            if "G1" in upper:
                return "G1"
            if "G2" in upper:
                return "G2"
    return None


def _scope_judgment(window_index: int, root_id: str) -> str:
    return f"""\
<scope>
Goal: Charter-runner CONSULT window {window_index} — external judgment consult on
agent-bus:{root_id}. The autonomous holder posted CONSULT_PENDING with
consult_role: judgment_gap; this window owns primary consult via
team_dispatch(model=cdp/opus-5) submit→poll_hint→provenance (depth-1 only —
no nested consult dispatch). Default executor: cursor-sdk Grok (unattended
generate wire; Opus reviewer via cdp/ model-endpoint). MCP project_ask = escape.
</scope>"""


def _scope_r_admit(window_index: int, root_id: str) -> str:
    return f"""\
<scope>
Goal: Charter-runner R-admit CONSULT window {window_index} — external R review on
agent-bus:{root_id}. The autonomous holder posted CONSULT_PENDING with
consult_role: r_admit; this window owns primary R-admit via
team_dispatch(model=cdp/opus-5) submit→poll_hint→E2 provenance (depth-1 only —
no nested consult dispatch). Default executor: cursor-sdk Grok (unattended
generate wire; Opus reviewer via cdp/ model-endpoint). MCP project_ask = escape.
</scope>"""


def _invariants_judgment(root_id: str) -> str:
    return f"""\
<invariants>
[consult-boundary] CONSULT_PENDING + consult_role: judgment_gap on
agent-bus:{root_id} — corpus + scope-lock fields are pinned on the root
CHECKPOINT; do not re-open scope.
[scope-lock] the consult ask is path-sim-shaped: Question (verbatim) · Out-of-scope
· detent · layers (L0/L1/L2 as declared) · deliverable gate. Do not invent a
tick-local ask grammar.
[consult-independence] fire the consult via team_dispatch(model=cdp/opus-5,
contract=light-bounded) to web-anthropic Opus — a DIFFERENT substrate/family than
the cursor-sdk seat running this window. Never self-answer the judgment gap.
MCP project_ask is escape only (IF6 / satellite-direct).
[sealed-unattended] CDP prompt MUST include the sealed unattended clause
(a:26156 / claude-ai-cdp-navigation § Sealed / unattended): answer with best
judgment; state assumptions; ¬ blocking wait for clarifying questions; ESCALATE
flag allowed when self-resolution fails (see [escalate-channel]).
[depth-1] cdp/ generate is the single depth-1 external boundary. Harvest one
consult reply; write shared provenance (consult_thread, verdict,
consultant_family, consultant_substrate) with consultant_family=anthropic /
consultant_substrate=web-anthropic (reviewer family, not this firing seat).
[escalate-channel] Sealed ¬ clarifying-questions forbids BLOCKING on a question,
not forbidding an escalation flag. When Opus cannot self-resolve, CHECKPOINT
verdict MAY be:
  ESCALATE(reason=<one line>, minimal_question=<one line>, provisional_verdict=<token>)
Opus still answers with best judgment + provisional_verdict; ESCALATE flags
human confirm. Do not wait for human in-window.
[OF2-resume] if the window ends mid-poll, Next-pickup MUST keep CONSULT_PENDING +
consult_role: judgment_gap + poll_hint / from=cdp bus-turn so the next tick
re-admits.
[window] end with exactly one CHECKPOINT on the root, then stop — no worker resume.
- Use the `consult-routing` skill (canonical slug — seat self-fetches)
- Use the `checkpoint-discipline` skill (canonical slug — seat self-fetches)
- Use the `path-sim` skill § Autonomous charter procession (consult-stop awareness)
</invariants>"""


def _invariants_r_admit(root_id: str) -> str:
    return f"""\
<invariants>
[consult-boundary] CONSULT_PENDING + consult_role: r_admit on agent-bus:{root_id} —
pinned R prompt URI / dense spec corpus on the root CHECKPOINT; do not re-open scope.
[R-independence] fire R-admit via team_dispatch(model=cdp/opus-5, contract=light-bounded)
to web-anthropic Opus — a DIFFERENT substrate/family than the cursor-sdk seat running
this window. Never self-assess R. MCP project_ask is escape only (IF6 / satellite-direct).
[sealed-unattended] R prompt MUST include the sealed unattended clause (a:26156 /
claude-ai-cdp-navigation § Sealed / unattended): answer with best judgment; state
assumptions; ¬ blocking wait for clarifying questions; ESCALATE flag allowed when
self-resolution fails (see [escalate-channel]). Cowork Qs false-complete
the harvest — do NOT auto-reply from this seat or the charter-runner.
[escalate-channel] Sealed ¬ clarifying-questions forbids BLOCKING on a question,
not forbidding an escalation flag. When Opus cannot self-resolve, CHECKPOINT
verdict MAY be:
  ESCALATE(reason=<one line>, minimal_question=<one line>, provisional_verdict=<token>)
Opus still answers with best judgment + provisional_verdict; ESCALATE flags
human confirm. Do not wait for human in-window. Transport of the flag is
pager/operator-proxy (out of this packet) — emit the shape so transport can carry it.
[depth-1] cdp/ generate is the single depth-1 external boundary (cross-family
model-endpoint ≠ nested SDK consult). Harvest one R reply; write the shared four-field
consult schema via `r_verdict_gate.consult_provenance_from_r_admit` with
consultant_family=anthropic / consultant_substrate=web-anthropic (reviewer family,
not this firing seat).
[OF2-resume] if the window ends mid-poll, Next-pickup MUST keep CONSULT_PENDING +
consult_role: r_admit + poll_hint / from=cdp bus-turn reference (replaces
execution_id-only resume used by project_ask escape) so the next tick re-admits.
[IF6-escape] if cdp/ cannot resume cross-window (lost poll_hint / bus turn), surface
IF6 and use MCP project_ask escape — holder-fired dual-host remains live; do not
delete emergency path.
[window] end with exactly one CHECKPOINT on the root, then stop — no worker resume here.
[stale-r-corpus-sha] Before firing cdp/, confirm CHECKPOINT Sidecars pins live
dense-spec hash on the **same row** as the dense-spec URI
(`Dense spec: cortex://… · spec_sha256:<64-hex>`). Machine pre-fire refuses
mismatch/missing/ambiguous/malformed/malformed_uri/unreadable (reason=stale_r_corpus_sha).
If refused: holder refreshes Sidecars (re-fs.read → rewrite hash → re-CHECKPOINT);
¬ this consult seat auto-rewrites Sidecars (a:26095).
- Use the `consult-routing` skill (canonical slug — seat self-fetches)
- Use the `checkpoint-discipline` skill (canonical slug — seat self-fetches)
- Use the `path-sim` skill § Autonomous charter procession (R-admit consult hosting)
</invariants>"""


def _task_guidance_judgment(
    *, root_id: str, scoreboard_line: str, identity_block: str
) -> str:
    return f"""\
<task_guidance>
## Resume step 0 (do first)
1. Load consult-routing + checkpoint-discipline +
   path-sim (§ Autonomous charter procession — consult-stop awareness).
2. {scoreboard_line}read the latest CHECKPOINT on agent-bus:{root_id} — confirm
   CONSULT_PENDING + consult_role: judgment_gap and pinned scope-lock: Question
   verbatim, OOS, detent, layers, deliverable gate, plus corpus manifest.

{identity_block}
## Judgment consult work (this seat owns submit→poll→provenance)
### Primary — team_dispatch model=cdp/opus-5
- Before fire: confirm the CDP prompt carries the sealed unattended clause
  (best judgment · state assumptions · ¬ blocking wait on clarifying questions ·
  ESCALATE allowed when self-resolution fails).
- If resuming: agent_bus.wait from the pinned poll_hint / from=cdp bus-turn until
  a qualifying cdp turn lands (reply or DELIVERY FAILED). Long running ≠ stalled.
- If fresh: team_dispatch(op=generate, model=cdp/opus-5, contract=light-bounded,
  prompt=<scope-locked Question + OOS + detent + layers + corpus>,
  dispatch_thread_id=…) → poll via agent_bus.wait from poll_hint (from_agent=cdp).
- Record on the root CHECKPOINT / todo attrs the **shared** provenance schema
  (same fields G3 R-admit writes): consult_thread, verdict, consultant_family,
  consultant_substrate (and evidence URI for the reply).
  consultant_family=anthropic / consultant_substrate=web-anthropic.
- On incomplete poll: Next-pickup = CONSULT_PENDING + consult_role: judgment_gap +
  poll_hint / from=cdp bus-turn anchor (OF2).

### Escape — MCP project_ask (IF6 / satellite-direct / holder emergency only)
- project_ask(op=submit, prompt_uri or inline prompt, converse=true,
  no_project_uuid=true, model=opus-5) → poll to content_proof/archive_uri.

## Acceptance criteria
1. One consult reply harvested with resolvable evidence URI.
2. Root CHECKPOINT updated with shared consult provenance fields.
3. Stop after CHECKPOINT — do NOT fire nested consults or cursor-sdk workers.

## Stop conditions
CHECKPOINT boundary · unresolvable cdp/ transport · IF6 tripwire ⇒ BLOCKED ·
ESCALATE when self-resolution fails (do not block in-window on a question).
</task_guidance>"""


def _window_identity_block(parsed: ParsedCheckpoint) -> str:
    """Visible arc identity for consult packets — distinct work must render distinct."""
    pickup_rows = normalize_next_pickup(parsed)
    pickup_line = pickup_rows[0] if pickup_rows else "(none)"
    ref = parsed.source_ref or "(unresolved)"
    return (
        "## Window identity (BINDING)\n"
        f"- normalized_next_pickup: {pickup_line}\n"
        f"- source_ref: {ref}\n"
    )


def _task_guidance_r_admit(
    *, root_id: str, scoreboard_line: str, identity_block: str
) -> str:
    return f"""\
<task_guidance>
## Resume step 0 (do first)
1. Load consult-routing + checkpoint-discipline + path-sim (§ R-admit consult hosting).
2. {scoreboard_line}read the latest CHECKPOINT on agent-bus:{root_id} — confirm
   CONSULT_PENDING + consult_role: r_admit and the pinned R prompt URI / corpus.

{identity_block}
## R-admit work (this seat owns submit→poll→E2)
### Primary — team_dispatch model=cdp/opus-5
- Before fire: confirm the R prompt carries the sealed unattended clause
  (best judgment · state assumptions · ¬ blocking wait on clarifying questions ·
  ESCALATE allowed when self-resolution fails).
- If resuming: agent_bus.wait from the pinned poll_hint / from=cdp bus-turn until
  a qualifying cdp turn lands (reply or DELIVERY FAILED). Long running ≠ stalled.
- If fresh: team_dispatch(op=generate, model=cdp/opus-5, contract=light-bounded,
  prompt/sidecar_ref=<R prompt cortex URI>, dispatch_thread_id=…) → poll via
  agent_bus.wait from poll_hint (from_agent=cdp).
- Parse merits verdict with fail-closed gate (ADMIT/RATIFY advance; amendments fold first).
  Question-shaped harvest without a merits enum ⇒ incomplete / keep CONSULT_PENDING
  (a:26156) — ¬ invent a verdict; ¬ auto-reply to Cowork clarifying questions.
- Write E2 via `consult_provenance_from_r_admit` — consultant_family=anthropic,
  consultant_substrate=web-anthropic regardless of this seat's substrate.
- On incomplete poll: Next-pickup = CONSULT_PENDING + consult_role: r_admit +
  poll_hint / from=cdp bus-turn anchor (OF2).

### Escape — MCP project_ask (IF6 / satellite-direct / holder emergency only)
- project_ask(op=submit, prompt_uri=cortex://…, converse=true, no_project_uuid=true,
  model=opus-5) → poll to content_proof/archive_uri; resume via execution_id.

## Acceptance criteria
1. R-admit harvested with content_proof/archive_uri (or cdp/ harvest URI) and
   parseable merits verdict.
2. Root CHECKPOINT carries the four shared consult provenance fields for implement_ready.
3. Stop after CHECKPOINT — no nested SDK consult fan-out.

## Stop conditions
CHECKPOINT boundary · unresolvable cdp/ transport · IF6 tripwire ⇒ BLOCKED ·
ESCALATE when self-resolution fails (do not block in-window on a question).
</task_guidance>"""


def _corpus(root_id: str, scoreboard_uri: str | None, *, r_admit: bool) -> str:
    design = (
        "cortex://notes/system/specs/charter-r-admit-consult-hosting.md"
        if r_admit
        else "cortex://notes/system/specs/charter-window-consult-hooks.md"
    )
    return f"""\
<corpus>
Charter root agent-bus:{root_id}. Scoreboard: {scoreboard_uri or '(see latest CHECKPOINT)'}.
Latest CHECKPOINT CONSULT_PENDING is the only state source.
Design: {design}.
</corpus>"""


_MCP_CAPABILITIES_CONSULT_HOST = """\
<mcp_capabilities>
LIFE/CORTEX MCP: ON — cortex, agent_bus, fs (cortex sandbox).
CODE/VORTEX MCP: ON — workspaces fs, team_dispatch (primary consult:
model=cdp/opus-5), agent_bus wait/poll_hint, project_ask (escape only).
This seat owns cdp/ submit→poll_hint (depth-1 external boundary). Nested
cursor-sdk consult fan-out is forbidden in this window.
</mcp_capabilities>"""


def _output_format_judgment(root_id: str, window_index: int) -> str:
    window_id = f"charter-{root_id}-w{window_index}"
    footer_req = output_format_footer_requirement(window_id=window_id)
    return f"""\
<output_format>
Post the CHECKPOINT on agent-bus:{root_id} with consult provenance fields filled.
Include consult_thread URI + verdict + consultant_family + consultant_substrate.
On incomplete poll preserve CONSULT_PENDING + consult_role: judgment_gap +
poll_hint / from=cdp bus-turn (or execution_id when on project_ask escape).
When self-resolution fails, emit ESCALATE(reason=…, minimal_question=…,
provisional_verdict=…) alongside best-judgment answer — do not block in-window.
Then stop — the next tick admits the worker resume window.
{footer_req}
</output_format>"""


def _output_format_r_admit(root_id: str, window_index: int) -> str:
    window_id = f"charter-{root_id}-w{window_index}"
    footer_req = output_format_footer_requirement(window_id=window_id)
    return f"""\
<output_format>
Post the CHECKPOINT on agent-bus:{root_id} with the four shared consult provenance
fields (consult_thread, verdict, consultant_family, consultant_substrate).
On incomplete poll preserve CONSULT_PENDING + consult_role: r_admit + poll_hint /
from=cdp bus-turn (or execution_id when on project_ask escape).
When self-resolution fails, emit ESCALATE(reason=…, minimal_question=…,
provisional_verdict=…) alongside best-judgment answer — do not block in-window.
Then stop — the next tick re-admits R-admit consult or worker resume after proof.
{footer_req}
</output_format>"""


def materialize_consult_packet(
    root_id: str,
    parsed: ParsedCheckpoint,
    *,
    scoreboard_uri: str | None = None,
    window_index: int = 1,
    arc_lane: str = "layer",
) -> str:
    """Return a depth-1 consult six-block packet for ``CONSULT_PENDING`` pickup."""
    scoreboard_line = (
        f"read the scoreboard at {scoreboard_uri}, then "
        if scoreboard_uri
        else ""
    )
    r_admit = _is_r_admit(parsed)
    footer = footer_kwargs_for_window(root_id, window_index)
    identity_block = _window_identity_block(parsed)
    if r_admit:
        body = f"""\
{_scope_r_admit(window_index, root_id)}
{_invariants_r_admit(root_id)}
{_task_guidance_r_admit(
    root_id=root_id,
    scoreboard_line=scoreboard_line,
    identity_block=identity_block,
)}
{_corpus(root_id, scoreboard_uri, r_admit=True)}
{_MCP_CAPABILITIES_CONSULT_HOST}
{_output_format_r_admit(root_id, window_index)}
"""
        return append_footer_to_packet(body, **footer)
    if arc_lane == "layer":
        gate_id = _open_layer_consult_gate(parsed)
        if gate_id is None:
            raise LayerConsultGateUnresolvedError("layer_consult_gate_unresolved")
        body = f"""\
{_scope_layer_judgment(window_index, root_id, gate_id=gate_id)}
{_invariants_layer_judgment(root_id, gate_id=gate_id)}
{_task_guidance_layer_judgment(
    root_id=root_id,
    scoreboard_line=scoreboard_line,
    identity_block=identity_block,
    gate_id=gate_id,
)}
{_corpus(root_id, scoreboard_uri, r_admit=False)}
{_MCP_CAPABILITIES_CONSULT_HOST}
{_output_format_judgment(root_id, window_index)}
"""
        return append_footer_to_packet(body, **footer)
    body = f"""\
{_scope_judgment(window_index, root_id)}
{_invariants_judgment(root_id)}
{_task_guidance_judgment(
    root_id=root_id,
    scoreboard_line=scoreboard_line,
    identity_block=identity_block,
)}
{_corpus(root_id, scoreboard_uri, r_admit=False)}
{_MCP_CAPABILITIES_CONSULT_HOST}
{_output_format_judgment(root_id, window_index)}
"""
    return append_footer_to_packet(body, **footer)


def consult_subject(
    root_id: str, window_index: int, *, consult_role: str | None = None
) -> str:
    """Bus subject for a charter consult admission (distinct from worker windows)."""
    if consult_role == "r_admit":
        return (
            f"Charter-runner R-admit consult window {window_index} — "
            f"agent-bus:{root_id} (CONSULT_PENDING — r_admit)"
        )
    return (
        f"Charter-runner consult window {window_index} — agent-bus:{root_id} "
        "(CONSULT_PENDING — judgment_gap via cdp/opus-5)"
    )


def consult_subject_for_arc(
    root_id: str,
    window_index: int,
    *,
    consult_role: str | None = None,
    arc_lane: str = "layer",
    gate_id: str | None = None,
) -> str:
    """Layer-aware consult subject when ``arc_lane=layer`` names Fable on G1."""
    if consult_role == "r_admit":
        return consult_subject(root_id, window_index, consult_role=consult_role)
    if arc_lane == "layer" and gate_id == "G1":
        return (
            f"Charter-runner layer consult window {window_index} — "
            f"agent-bus:{root_id} (CONSULT_PENDING — G1 architecture via cdp/fable)"
        )
    return consult_subject(root_id, window_index, consult_role=consult_role)


__all__ = ["consult_subject", "consult_subject_for_arc", "materialize_consult_packet"]
