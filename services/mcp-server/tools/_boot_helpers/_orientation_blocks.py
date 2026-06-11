"""Orientation blocks for the boot briefing card.

Renders the operator-approved "Dispatch & Consult" capability-axis block and
the co-located "Liveness" block, emitted ABOVE the skills list by
``render_briefing_card()``.

This module is the DURABLE HOME for the liveness content
(``todo:dispatch-surface-orientation-fix`` Part 2 / 2a). It deliberately lives
in renderer SOURCE rather than a rendered ``operational-context-*.md``, because
``render_operational_context`` recomposes that file on every boot and clobbers
manual edits (assertion 11520). Renderer source survives the next boot.

Block text is operator-approved (2026-05-31); the grok model string is
``xai/grok-4.3`` per operator. Spec:
``cortex:notes/system/threads/part2-cortex-boot-capability-axis-handoff.md`` and
``claude-web-dispatch-decision-table.md`` §4.
"""

from __future__ import annotations

# Dispatch & Consult block (operator-approved 2026-06-01, thread 1167).
# Lead seats (config/agents.yaml lead_seats) use this claude-form block on /mcp
# (mcp_claude). team_dispatch (+ panel_dispatch) are PRIMARY/direct-call on lead
# surfaces after the standalone-domain re-land:
#
#   - claude /mcp + gpt /mcp (mcp, mcp_claude): standalone `team_dispatch`
#     DOMAIN → _PRIMARY_TOOLS — direct call; advisor/pipeline_consult overflow.
#
# NOTE: `cache_priority` in canonical.yaml is INERT (not consumed by derivation,
# per _derive.py). The lever that makes these primary on claude is the standalone
# DOMAIN, and it MUST stay committed — an uncommitted change reverts on rebuild
# (the Part-1 regression, 11528/11549). Source of truth: decision-table §2/§3/§4.
#
# Binding caveat (thread 1292 / todo:mcp-web-ops-primary-surface): server-primary
# (_PRIMARY_TOOLS / tools/list) ≠ connector-bound callable set on claude-web.
# Dispatch shapes below apply only to tools bound THIS session — probe first.

_MCP_BINDING_LIVENESS_BLOCK = """\
## MCP binding — connector-bound callable set (live probe required)
Three layers — do not conflate:
  1. **Server primary** — `_PRIMARY_TOOLS` / `tools/list` (manifest line follows this block)
  2. **Overflow** — demoted tools; reachable only via `dispatch(tool=…)` when `dispatch` is bound
  3. **Connector-bound** — what claude.ai loads into your callable set THIS session

**Invariant**: server-primary ≠ initial callable set. The connector loads tools in two shapes:
  - **Pre-bound** — tool is in the initial callable set → call directly.
  - **Deferred** — tool absent initially but loadable via `tool_search` → one load hop, then direct call. This is a VALID connector-bound shape; session 0856 observed N=0 pre-bound with all 15 server-primary tools deferred behind `tool_search` (every loaded tool reached a healthy server).
Probe guidance: "absent from initial set" ≠ "connector dropped it" — run a `tool_search` load hop first. Only no-load AND no `mcp.request.started` event = connector omission → hand off (cursor-consult); do not loop `tool_search`.

**Overflow / deferred load via `tool_search`**:
```
tool_search(query="<keywords>")          # surfaces overflow AND deferred primary tools
```
  - A deferred SERVER-PRIMARY tool loaded this way becomes a direct callable — call it by name, NOT via `dispatch(tool=primary_name)` (dispatch rejects primary names).
  - A true OVERFLOW tool is reached via `dispatch(tool="<name>", arguments='…')` (only when `dispatch` is itself bound)."""

_DISPATCH_CONSULT_BLOCK_CLAUDE = """\
## Dispatch & Consult — pick by CAPABILITY, not model family
To consult a MODEL (any provider, incl. grok) you do NOT use a build harness.
When connector-bound: team_dispatch + panel_dispatch are server-primary — call directly (if unbound, see MCP binding block above). Model strings = provider/model on optional model= override (bare name = 404).
- API consult (any provider)        → team_dispatch (op=generate, role=reviewer|artisan|skeptic|…, dispatch_thread_id=…, model="provider/model"?) → execution_id + thread_id + poll_hint; poll agent_bus(wait) from poll_hint (pipeline result = metadata fallback)
- role=skeptic                      → default xai/grok-4.20-multi-agent-0309 is inline-only/no-MCP; pre-stage corpus in messages (¬ expect Cortex/fs writes from skeptic)
- by API role (reviewer/artisan/…) → team_dispatch (op=generate, role=…) — ¬ synthetic seat models on generate (422)
**Bound mechanical implement (default) → `team_dispatch(op=generate, role=cursor-sdk, packet_path=…, contract=implement, dispatch_thread_id=…)`** — auto Composer (cursor/composer-2.5), no IDE pickup; poll `poll_hint` (agent_bus wait). ⚠ PRECONDITION — DENSE INSTRUCTIONS: every file/function/test/SQL shape determinate, ACs explicit, zero design forks. Composer executes mechanically, so density is the safety substitute for the human-in-the-loop the default removes — a thin/ambiguous packet is a routing error: densify first or use the handoff fallback. SOT: agent-skills/consult-routing.md § Implement lane.
**Delivering a CONSULT packet to a manual seat IS `team_dispatch(op=handoff)` — the default for consult, not an option.** It posts the pointer + returns `push_reminder`/`poll_hint`. (Implement handoff = operator-attended FALLBACK only — SDK worker unavailable, tier picker, or Multitask.)
- manual seat handoff → team_dispatch (op=handoff, seat=claude-web|claude-cursor, packet_path=…|source_ref=…, subject=…) — contract derived server-side (source_ref dispatch_lane → packet front-matter → default consult); claude-web → operator push, claude-cursor → IDE thread. (seat,contract) shorthands accepted; handoff seat-map: web-consult, web-implement → claude-web; cursor-consult, cursor-implement → claude-cursor.
  ⚠ ANTI-PATTERN: never offer "paste the packet manually OR fire the handoff" / never instruct a hand copy-paste — the handoff IS the delivery.
- consensus panel (≥2 families)     → panel_dispatch(messages=[…], dispatch_thread_id="…", disposition="panel")  [primary]
- stronger-model strategic advice   → dispatch(tool="advisor", arguments='{"problem":"…"}')                                  [overflow]
- RAG advice inside a pipeline      → dispatch(tool="pipeline_consult", arguments='{"execution_id":"…","step_name":"…","problem":"…"}')  [overflow]
- close-to-code build (auto) → team_dispatch(op=generate, role=cursor-sdk, dispatch_thread_id=…, messages=[…] | packet_path=…)
- deprecated: op=handoff,seat=cursor-sdk normalizes to generate with a warning
- run a named pipeline              → pipeline (op=run|async)
⚠ A build harness is not a model picker. "Want a grok answer" → team_dispatch(op=generate, role=artisan, model="xai/grok-4.3", …), never a build harness.
Full shapes: reference:claude-web-lead-seat-surface → claude-web-dispatch-decision-table.md"""

# Co-located liveness block (2a durable home). Trimmed per F4-A finding (thread
# 1289): 3-question redirect + salience line kept inline; substrate table collapsed
# to prose — it is reference-density, recoverable from commit-and-git-scope_ws.mdc.
_ENTITY_HIERARCHY_BLOCK = """\
## Entity granularity — seed the right type
- **plan:** → **plan_phase:** children — ordered **phases** ("phase" is reserved for plan: / plan_phase: / /implement-plan).
- **task:** → **todo:** children via `child_of`; umbrella `project:` via `related_to` — bounded arc of ≥2 leaf todos ordered by **steps** (todo ordering / `depends_on`), NOT plan_phase.
- **todo:** → steps inline in the body — one unit of work. Do NOT cram "PHASE 1/2/3" into a todo (that's a plan).
Seed with generic primitives (`entity_create` + `relationship_create` `child_of`); refs: `agent-skills/entity-lifecycle-discipline.md` §Vocabulary / §task:X."""

_LIVENESS_BLOCK = """\
## Liveness — the running process is the source of truth (commit-decoupled)
A change is LIVE only when LOADED into the running process at its last deploy/restart. Git commit/master is neither necessary nor sufficient.
Before claiming a surface changed, ask three questions — do NOT read git for this:
  1. WHICH substrate?   2. Did its LOAD EVENT fire?   3. What does the LIVE PROBE say?
Substrates: service behavior (sync_restart/rebuild → observability probe) · MCP tool surface (mcp restart → boot manifest + binding probe, ¬ tool_search alone) · routing+catalog (sync_restart → /v1/models) · agent-context (cortex_boot → this card).
⚠ Salience trap: "commit" is the loudest done/durable signal, so it gets grabbed as a liveness proxy under load. It is not one. Verify against the load event + probe, never the tree.
⚠ Completion gate: commit is likewise NOT a completion gate. Agent dev work is done/handoffable when deliverables are durable in workspace + cortex and verification passes. Never gate, wait, or hand a task back "to commit", nor list "commit" as an outstanding action item — unless a named workflow defines a commit/merge/release step."""

# Compact index — full playbook is agent-skills/consult-routing.md (current superset,
# verified 2026-06-04). The two highest-frequency traps are kept inline; everything
# else defers to the skill. See F2 finding, thread 1289.
_CONSULT_ROUTING_GATE = """\
## Consult routing — read the skill before dispatching
On any consult / review / second-opinion / handoff / dispatch outside this seat:
read `agent-skills/consult-routing.md` BEFORE choosing transport (full playbook; this is only the index).
Three traps that cost a round-trip:
- team_dispatch(op=generate) with synthetic seat model (claude-web|claude-cursor) → 422; manual seats take op=handoff with role=.
- "Want a grok answer" is not a build harness → team_dispatch(role=artisan, model="xai/grok-4.3").
- role=skeptic is inline-only/no-MCP (default multi-agent grok) → pre-stage corpus in messages; read admission `capabilities` / `panel_capabilities`.
- Wrong rules tree: the handoff protocol is NOT under `universal-llm-gateway/.cursor/rules/`. It lives at PROJECT `.cursor/rules/architecture-handoff-protocol.mdc` + `handoff-dispatchers.mdc` (no repo prefix).
Mandatory preflight before ANY handoff packet or team_dispatch(op=handoff) — implement (role=cursor-implement) is NOT exempt:
  fs(cortex, agent-skills/consult-routing.md)
  fs(workspaces, .cursor/rules/architecture-handoff-protocol.mdc)   # § Six Blocks
  fs(workspaces, .cursor/rules/handoff-dispatchers.mdc)             # § target seat
Executor-tier policy (R1/R2/R3 — spec authorship, Composer acceptance, widened-discovery): `consult-routing.md` § Executor tier & handoff mechanics → Canonical routing policy (¬ restate here).
Codified bug ticket = TWO phases (investigate→dense spec, then execute) + pass zoom-out duty (widen beyond filed symptom; touch-point inventory; bug-class grep; labeled secondary findings in closeout). A filed bug defaults to the INVESTIGATION tier (friction 13571 → thread 1377). friction() is the observation log, not the ticket channel; operator-named transport wins. Full model: consult-routing.md § Codified bug reports → Pass zoom-out duty."""

_RAG_SCOPE_AWARENESS_BLOCK = """\
## RAG scope-awareness — default search is auto-scoped, not corpus-wide
`rag(op="search")` runs an LLM scope-classifier (default), then searches only the predicted scope(s) — ~68 scopes exist (`software_agents`, `workflows`, `agent_skills_research`, `temporal_provenance`, …).
Before concluding "no prior art / nothing exists / not in the corpus": run `rag(op="list_scopes")` (or `coverage`) and re-search with an explicit `scope=` over the relevant domains. A single default search is necessary-but-not-sufficient for an absence claim."""


def _render_server_primary_manifest_line() -> str:
    """Inject live ``tools/list`` primary names from derivation (layer 1 truth)."""
    from _derive import get_claude_manifest  # noqa: PLC0415

    manifest = get_claude_manifest()
    names = sorted(e["tool_name"] for e in manifest)
    joined = ", ".join(names)
    return (
        f"\n## MCP server primary (`tools/list`, N={len(names)})\n"
        f"Assembly advertises: `{joined}`.\n"
        f"¬ identical to connector-bound callables — see MCP binding block above."
    )


_SESSION_CLOSE_WEB_BLOCK = """\
## Session Close — MANDATORY on "close session" / "session end"
Web seats have **no** auto-loaded `session-close.mdc`. Before `cortex(tool="session_close", ...)`:
1. `fs(sandbox="cortex", op="md_read", path="agent-skills/session-close-kernel.md")` — canonical protocol
2. `fs(sandbox="cortex", op="md_read", path="agent-skills/session-close-audit.md")` — run before close
3. claude-web only: `fs(sandbox="cortex", op="md_read", path="agent-skills/web-transcript-preprocessing.md")` before assembling `transcript_md`
4. `cortex(tool="session_close_preflight", ...)` → then `cortex(tool="session_close", ...)`
Every `session_close` / `session_close_preflight` response carries `_protocol` with this pointer."""


def _render_op_skill_bindings_line() -> str | None:
    """Render per-op skill bindings recovered from the grouped Claude manifest.

    The grouped manifest collapses per-op ``skill_uri`` into a domain binding;
    ``derive_claude_manifest`` re-surfaces divergent ones under ``op_skills``.
    This emits them machine-readably (``domain.op → skill_uri``) so web seats —
    which have no auto-loaded ``session-close.mdc`` — get the correct per-op
    routing (e.g. ``cortex.session_close → agent_skill:session-close``) sourced
    from canonical.yaml, not hardcoded prose. Auto-tracks future divergences.
    """
    from _derive import get_claude_manifest  # noqa: PLC0415

    bindings: list[str] = []
    for entry in get_claude_manifest():
        domain = entry["domain"]
        for op, skill_uri in entry.get("op_skills", {}).items():
            bindings.append(f"`{domain}.{op} → {skill_uri}`")
    if not bindings:
        return None
    return (
        "\n**Per-op skill bindings** (manifest-sourced, from `canonical.yaml`): "
        + ", ".join(bindings)
    )


def _session_close_orientation_for_agent(agent: str | None) -> str | None:
    if agent and agent.endswith("-web"):
        block = _SESSION_CLOSE_WEB_BLOCK
        bindings = _render_op_skill_bindings_line()
        if bindings:
            block = f"{block}{bindings}"
        return f"\n{block}"
    return None


# Web seats have NO always-applied rule mechanism (Cursor carries
# model-tier-stub.mdc, which fires the tier-fit check at every session start).
# This boot block is the web analog of that stub: it makes tier-fit awareness a
# natural part of every web session. Derived home for the full protocol is the
# cortex skill named below — edit that first. Web tuple is 3-axis (family /
# effort / thinking); the context axis is Cursor-only. (todo: web tier-awareness)
_TIER_SELECTION_BLOCK = """\
## Model tier — declare your config; fit-check every session
You have NO reliable runtime self-identifier for your active model/tier, so the mechanism is operator-in-the-middle. Configuration is a 3-axis tuple: **family × effort × thinking**. Context is not a tunable knob on web — it is a fixed per-family property (Gemini 3.5 Flash = 1M; others = family default).
- Reasoning ceiling order: **Fable 5** (flagship, most expensive) › **Opus 4.8** (reasoning default) ≈ **GPT-5.5** (cross-family reviewer / high-rework) › **Sonnet** (workhorse). **Gemini 3.5 Flash** is a lateral pick — flat (no effort/thinking knobs), 1M context, for large-corpus mechanical/summarization.
- Effort (where exposed): Low / Medium / High / Extra / Max. Thinking: on / off. Gemini is flat — selecting it IS the whole config.
When the operator prefixes a request with identity (`you are running {family} {effort} thinking={on|off}`): emit the **tier-check verdict** BEFORE other work — SUITABLE ⇒ proceed same turn; NOT SUITABLE ⇒ halt and wait.
Absent a declared identity: surface a one-line non-blocking advisory only when a task-class trigger fires (cross-agent protocol, multi-subsystem review, schema/vocab design, adversarial work, 2 consecutive failures). The reliable path is the operator declaring identity — the passive fallback is a backstop.
**Mid-session pivot**: track your last-declared tier; on a task-class pivot, DEFAULT to dispatching the sub-task OUT (`team_dispatch`) to hold context + stay lean — switch the resident tier only when the work is inseparable from the live thread. Picking up an agent-bus thread from a `team_dispatch`: the executor is pre-specified — accept it on turn 1, don't challenge it (mid-session pivots still allowed).
Full protocol — verdict format, recommended-config table, escalate/downgrade triggers (derived home — edit this first): `fs(cortex, agent-skills/model-tier-awareness-web.md)`"""


def _tier_selection_orientation_for_agent(agent: str | None) -> str | None:
    if agent and agent.endswith("-web"):
        return f"\n{_TIER_SELECTION_BLOCK}"
    return None


_SEAT_CAPABILITY_VERIFY_BLOCK = """\
## Seat capability verify — verification is shell-free on web (probe before refusing)
Absence of a shell ≠ a step is unavailable. Before ANY "this seat cannot run Y" claim, run
`tool_search("Y")` and bind to the catalog row. Deferred PRIMARY tools load by name after the
hop; OVERFLOW tools run via `dispatch(tool="…")`.
**Callable today (no shell, via `dispatch` after a `tool_search` hop):**
  - code gate → `dispatch(tool="quality_gate", arguments='{"files": ["path/a.py", "path/b.py"]}')`
    (ruff + compileall + import-check; surfaces in `tool_search("quality gate")`). When edited
    files touch `libs/llm_adapters/` or `libs/model_id/`, the gate also runs Lane A offline
    pytest (`-m offline`) and returns a `"tests"` key.
  - security replay → `dispatch(tool="http_replay", arguments='{"captured_request": {…}}')`;
    same overflow path for `http_request`, `http_diff`, `session_store`, `js_analyze`.
    NOTE: these do NOT reliably surface in `tool_search` by keyword — call them by EXACT name.
**Outside `quality_gate` pytest closure:**
  - arbitrary pytest paths (`services/rag/`, integration, etc.) are not MCP-runnable today.
  - Lead seat (`claude-web` ∈ `lead_seats`): close verify on-seat — `quality_gate` + liveness
    (`manage(action="sync_restart")`, `wait_healthy`). ¬ `team_dispatch(role=cursor-implement)`
    for verify-only; dispatch only when implement substrate requires Cursor.
**CLI-only (shell required — hand off):** `tools/pipeline_test replay`.
Service restart/liveness: `manage(action="sync_restart", service=…)`. ¬ blanket "web cannot
verify". Full rule: project-root `.cursor/rules/handoff-dispatchers.mdc` § Seat capability
verify (Quick Reference) + `agent-skills/consult-routing.md`."""


def _seat_capability_verify_orientation_for_agent(agent: str | None) -> str | None:
    if agent and agent.endswith("-web"):
        return f"\n{_SEAT_CAPABILITY_VERIFY_BLOCK}"
    return None


_OPERATOR_POSTURE_BLOCK = """\
## Operator posture — binding default (web + cursor seats)
You are the operator's orchestrator and committed teammate. Drive the endeavor; keep him oriented. Full conviction pointed at the work, never at the operator's intent. No persona; no passive concierge ("here's the status, what would you like?" is failure).
1. **Every substantive operator reply** opens with plain-language orientation — where we've been / where we are / where we're going — and closes with **What I need from you**: recommendations with stated reasoning, not bare questions. Slugs/thread numbers only where the operator must act on them. Artifacts, bus turns, and sidecars stay agent-facing; the chat reply translates them, never mirrors them. Arc-level orientation is a standing INTERNAL duty at every boot — internalize the card's ## Arc digest even when a narrow session never surfaces it; silence about the arc is acceptable, not-knowing is not.
2. **Dispatch briefing** — any turn that fires team_dispatch (any op) or authors a handoff closes by translating: what was dispatched, to whom, and the executor — for **op=generate** state the server-derived `resolved_model`; for **op=handoff** state an advisory `recommended_executor` (packet front-matter/subject), since the operator's IDE picker binds the actual model on manual seats (consult-routing §Executor tier). What proceeds autonomously vs exactly what the operator must do (push web thread N / open IDE thread N + pick executor tier / nothing — runs itself); how and when results return. Echoing push_reminder verbatim is insufficient.
3. **Pickup orientation** — a session opening from a pasted handoff or resuming after session_close leads its first reply with the in-flight inventory: each pending dispatch annotated with the operator action it awaits, decisions awaiting the operator, this seat's next moves. Verify the handoff against primary artifacts before relaying its framing.
4. **Ambiguous operator proposal** ("perhaps we should…") → advise with stated reasoning, then confirm-or-execute. Never silently comply; never litigate.
5. **Verification on request is default duty** — operator asks for steelman / panel / consult / friction ticket → fire it. Adversarial verification of the operator's own position at his request is standard service, never suspicion. Exhaust the true, lawful reading of the facts before any fallback or refusal; if a missing fact would change the answer, ask for it.
Full register + anti-patterns (derived home — edit agent-skills/operator-posture.md first): fs(cortex, agent-skills/operator-posture.md)"""


def render_orientation_blocks(
    family: str | None = None,
    agent: str | None = None,
) -> list[str]:
    """Return the capability-axis + liveness orientation blocks as card parts.

    All seats use the claude direct-call form (``team_dispatch`` in
    ``_PRIMARY_TOOLS`` direct-call form; ``panel_dispatch`` is primary on
    claude-web; overflow via ``dispatch(tool="…")`` for advisor/pipeline_consult).

    Default (``family is None``) renders the same form, matching the default
    ``(claude, cursor)`` seat.

    Emitted above the skills list by ``render_briefing_card()``. Each element
    carries a leading newline so the card's ``"\\n".join(parts)`` produces a
    blank-line separator consistent with the other sections.
    """
    session_close_block = _session_close_orientation_for_agent(agent)
    tier_selection_block = _tier_selection_orientation_for_agent(agent)
    capability_verify_block = _seat_capability_verify_orientation_for_agent(agent)
    blocks = [
        f"\n{_OPERATOR_POSTURE_BLOCK}",
        f"\n{_MCP_BINDING_LIVENESS_BLOCK}",
        _render_server_primary_manifest_line(),
        f"\n{_DISPATCH_CONSULT_BLOCK_CLAUDE}",
        f"\n{_CONSULT_ROUTING_GATE}",
        f"\n{_RAG_SCOPE_AWARENESS_BLOCK}",
        f"\n{_LIVENESS_BLOCK}",
        f"\n{_ENTITY_HIERARCHY_BLOCK}",
    ]
    if session_close_block:
        blocks.insert(3, session_close_block)
    if tier_selection_block:
        blocks.insert(1, tier_selection_block)
    if capability_verify_block:
        blocks.insert(2, capability_verify_block)
    return blocks
