"""Build the depth-1 consult-seat six-block packet for one charter window.

When the latest CHECKPOINT stop is ``CONSULT_PENDING``, the tick admits a consult
seat — dual-wire under the same ``window_kind=consult``:

- ``consult_role: judgment_gap`` → web-consult handoff (scope-lock harvest, no
  R-admit transport).
- ``consult_role: r_admit`` → unattended cursor-sdk generate; primary R host is
  ``team_dispatch(model=cdp/opus-5)`` with MCP ``project_ask`` as escape only.

Consult seats must not dispatch nested consults (depth-1 only).
"""

from __future__ import annotations

from .checkpoint_parse import ParsedCheckpoint
from .residue_fingerprint import normalize_next_pickup

ConsultRole = str  # r_admit | judgment_gap


def _is_r_admit(parsed: ParsedCheckpoint) -> bool:
    return parsed.consult_role == "r_admit"


def _scope_judgment(window_index: int, root_id: str) -> str:
    return f"""\
<scope>
Goal: Charter-runner CONSULT window {window_index} — external consult on
agent-bus:{root_id}. The autonomous holder posted CONSULT_PENDING; this window
is the depth-1 consult seat only (single round, no nested consult dispatch).
Default executor: web-consult (web-anthropic).
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
[consult-boundary] CONSULT_PENDING stop on agent-bus:{root_id} — corpus +
scope-lock fields are pinned on the root CHECKPOINT; do not re-open scope.
[scope-lock] the consult ask is path-sim-shaped: Question (verbatim) · Out-of-scope
· detent · layers (L0/L1/L2 as declared) · deliverable gate. Do not invent a
tick-local ask grammar.
[depth-1] harvest exactly one consult reply; write verdict + consultant_family +
consultant_substrate (+ consult_thread URI) onto the root CHECKPOINT / todo
attrs; then STOP. This seat must NOT dispatch team_dispatch/cursor-sdk consults.
[verdict-grammar] emit a path-sim merits verdict token (ADMIT | ADMIT_WITH_AMENDMENTS
| RATIFY | RATIFY_WITH_CONDITIONS | RETURN | SCOPE-DRIFT) — same grammar R-admit
uses so implement_ready / r_verdict_gate share one parser surface.
[independence] you are the external consult seat — your family/substrate must
differ from the autonomous cursor-sdk holder that fired CONSULT_PENDING.
[window] end with exactly one CHECKPOINT on the root (from=web-anthropic or the
resolved consult seat), then stop — no worker resume in this window.
- Use the `consult-routing` skill (canonical slug — seat self-fetches)
- Use the `agent-bus-discipline` skill (canonical slug — seat self-fetches)
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
assumptions; ¬ clarifying questions; ¬ wait for a human. Cowork Qs false-complete
the harvest — do NOT auto-reply from this seat or the charter-runner.
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
- Use the `agent-bus-discipline` skill (canonical slug — seat self-fetches)
- Use the `path-sim` skill § Autonomous charter procession (R-admit consult hosting)
</invariants>"""


def _task_guidance_judgment(*, root_id: str, scoreboard_line: str) -> str:
    return f"""\
<task_guidance>
## Resume step 0 (do first)
1. Load consult-routing + agent-bus-discipline (§ Standing root threads) +
   path-sim (§ Autonomous charter procession — consult-stop awareness).
2. {scoreboard_line}read the latest CHECKPOINT on agent-bus:{root_id} — confirm
   CONSULT_PENDING and pinned scope-lock: Question verbatim, OOS, detent, layers,
   deliverable gate, plus corpus manifest.

## Consult work (depth-1)
- Answer the pinned Question; respect declared Out-of-scope; honor detent/layers
  and the deliverable gate (do not invent a parallel ask shape).
- Post your reply on the consult thread referenced by the CHECKPOINT (or create
  one and cite agent-bus:{{tid}} in the root CHECKPOINT Sidecars).
- Record on the root CHECKPOINT / todo attrs the **shared** provenance schema
  (same fields G3 R-admit writes): consult_thread, verdict, consultant_family,
  consultant_substrate (and evidence URI for the reply).

## Acceptance criteria
1. One consult reply harvested with resolvable evidence URI.
2. Root CHECKPOINT updated with shared consult provenance fields for implement_ready.
3. Stop after CHECKPOINT — do NOT fire nested consults or cursor-sdk workers.

## Stop conditions
CHECKPOINT boundary · unresolvable consult transport · depth>1 ask ⇒ BLOCKED.
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
1. Load consult-routing + agent-bus-discipline + path-sim (§ R-admit consult hosting).
2. {scoreboard_line}read the latest CHECKPOINT on agent-bus:{root_id} — confirm
   CONSULT_PENDING + consult_role: r_admit and the pinned R prompt URI / corpus.

{identity_block}
## R-admit work (this seat owns submit→poll→E2)
### Primary — team_dispatch model=cdp/opus-5
- Before fire: confirm the R prompt carries the sealed unattended clause
  (best judgment · state assumptions · ¬ clarifying questions · ¬ wait for human).
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
CHECKPOINT boundary · unresolvable cdp/ transport · IF6 tripwire ⇒ BLOCKED.
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


_MCP_CAPABILITIES_JUDGMENT = """\
<mcp_capabilities>
LIFE/CORTEX MCP: ON — cortex, agent_bus, fs (cortex sandbox).
CODE/VORTEX MCP: ON — workspaces fs for read-only corpus inspection.
This consult seat must NOT use team_dispatch generate/cursor-sdk or nested SDK
consult under the autonomous holder (forbidden — depth-1 window boundary only).
</mcp_capabilities>"""


_MCP_CAPABILITIES_R_ADMIT = """\
<mcp_capabilities>
LIFE/CORTEX MCP: ON — cortex, agent_bus, fs (cortex sandbox).
CODE/VORTEX MCP: ON — workspaces fs, team_dispatch (primary R-admit:
model=cdp/opus-5), agent_bus wait/poll_hint, project_ask (escape only).
This seat owns cdp/ submit→poll_hint (depth-1 external boundary). Nested
cursor-sdk consult fan-out is forbidden in this window.
</mcp_capabilities>"""


def _output_format_judgment(root_id: str) -> str:
    return f"""\
<output_format>
Post the CHECKPOINT on agent-bus:{root_id} with consult provenance fields filled.
Include consult_thread URI + verdict + consultant_family + consultant_substrate.
Then stop — the next tick admits the worker resume window.
</output_format>"""


def _output_format_r_admit(root_id: str) -> str:
    return f"""\
<output_format>
Post the CHECKPOINT on agent-bus:{root_id} with the four shared consult provenance
fields (consult_thread, verdict, consultant_family, consultant_substrate).
On incomplete poll preserve CONSULT_PENDING + consult_role: r_admit + poll_hint /
from=cdp bus-turn (or execution_id when on project_ask escape).
Then stop — the next tick re-admits R-admit consult or worker resume after proof.
</output_format>"""


def materialize_consult_packet(
    root_id: str,
    parsed: ParsedCheckpoint,
    *,
    scoreboard_uri: str | None = None,
    window_index: int = 1,
) -> str:
    """Return a depth-1 consult six-block packet for ``CONSULT_PENDING`` pickup."""
    scoreboard_line = (
        f"read the scoreboard at {scoreboard_uri}, then "
        if scoreboard_uri
        else ""
    )
    r_admit = _is_r_admit(parsed)
    if r_admit:
        identity_block = _window_identity_block(parsed)
        return f"""\
{_scope_r_admit(window_index, root_id)}
{_invariants_r_admit(root_id)}
{_task_guidance_r_admit(
    root_id=root_id,
    scoreboard_line=scoreboard_line,
    identity_block=identity_block,
)}
{_corpus(root_id, scoreboard_uri, r_admit=True)}
{_MCP_CAPABILITIES_R_ADMIT}
{_output_format_r_admit(root_id)}
"""
    return f"""\
{_scope_judgment(window_index, root_id)}
{_invariants_judgment(root_id)}
{_task_guidance_judgment(root_id=root_id, scoreboard_line=scoreboard_line)}
{_corpus(root_id, scoreboard_uri, r_admit=False)}
{_MCP_CAPABILITIES_JUDGMENT}
{_output_format_judgment(root_id)}
"""


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
        "(CONSULT_PENDING — web-consult)"
    )


__all__ = ["consult_subject", "materialize_consult_packet"]
