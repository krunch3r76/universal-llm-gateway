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

from agent_seat.inject_channels import ORIENTATION_BLOCK_SKILL_MAP

# inject-channel block keys → slugs: agent_seat.inject_channels.ORIENTATION_BLOCK_SKILL_MAP
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

# inject-channel block key: gates-strip
_GATES_STRIP = """\
## GATES — fire BEFORE any tool call
1. **MCP binding** — server-primary ≠ connector-bound callable set. Call primaries **by name first**; empty server `tool_search` ≠ absent. ¬ route primary names through `dispatch`.
2. **Consult routing** — on ANY consult / review / handoff / dispatch outside this seat: `fs(cortex, agent-skills/consult-routing.md)` BEFORE choosing transport (mandatory preflight for handoff packets)."""

_GATES_CAPABILITY_VERIFY_LINE = (
    "3. **Capability verify (web)** — before ANY \"this seat cannot run Y\" claim: "
    "`tool_search(\"Y\")` then `quality_gate`; lead seats close-verify on-seat."
)

# inject-channel block key: mcp-binding-block
_MCP_BINDING_LIVENESS_BLOCK = """\
## MCP binding — connector-bound callable set (live probe required)
Three layers — do not conflate: (1) **Server primary** — `_PRIMARY_TOOLS`/`tools/list` (manifest line follows); (2) **Overflow** — reachable via `dispatch(tool=…)` when `dispatch` is bound; (3) **Connector-bound** — what claude.ai loads into your callable set THIS session.
**Invariant**: server-primary ≠ initial callable set. Two connector shapes: **pre-bound** (in initial callable set → call directly) and **deferred** (absent initially but may load on first **direct** call by name — a VALID shape; N=0 pre-bound with all primaries deferred is normal, not a drop).

**Two different `tool_search` mechanisms — do not conflate:**
1. **Connector deferred-tools block** (Anthropic system prompt) — platform bootstrap for lazy-loading MCP primaries; emits no server event.
2. **Server `tool_search` MCP tool** — indexes **overflow only** (sql, web_fetch, advisor, …). It does NOT list or load server primaries (`fs`, `rag`, `dispatch`, `team_dispatch`, …).

If `fs`/`rag`/any primary looks absent from your pre-bound set: **call it directly by name first** (e.g. `fs(sandbox="cortex", op="md_list", path="agent-skills/fs.md")`). Do NOT conclude unavailability from an empty server `tool_search` result — that is expected for primaries. ¬ route primary names through `dispatch` (it rejects them).

Genuine omission only if a **direct** primary call fails at the connector (tool unavailable / no `mcp.request.started`) → log friction, refresh connector, or hand off (cursor-consult)."""

# inject-channel block key: dispatch-consult-block
_DISPATCH_CONSULT_BLOCK_CLAUDE = """\
## Dispatch & Consult — pick by CAPABILITY, not model family
To consult a MODEL (any provider, incl. grok) you do NOT use a build harness. When connector-bound, `team_dispatch`/`panel_dispatch` are server-primary — call directly (if unbound, see MCP binding block). Model strings = `provider/model` on optional `model=` override (bare name = 404).
- **API consult / role** (reviewer|artisan|skeptic|…) → pre-stage context on an agent-bus thread; `team_dispatch(op=generate, role=…, dispatch_thread_id=<thread>, model="provider/model"?)` → execution_id + poll_hint; poll `agent_bus(wait)`. ¬ synthetic seat models on generate (422). role=skeptic (grok) is inline-only/no-MCP — pre-stage corpus in messages.
- **Mechanical implement (default)** → `team_dispatch(op=generate, role=cursor-sdk, source_ref=todo:{slug}, contract=implement, dispatch_thread_id=…)` — server materializes from distilled todo attrs; auto Composer, no IDE pickup. PRECONDITION: dense, determinate instructions (Composer executes mechanically; thin packet = routing error). `packet_path=` is the named exception.
- **Manual-seat handoff (consult default)** → `team_dispatch(op=handoff, seat=claude-web|claude-cursor, source_ref=…|packet_path=…, subject=…)`; web→operator push, cursor→IDE thread. The handoff IS the delivery — never instruct a manual copy-paste.
- **Panel** (≥2 families) → `panel_dispatch(messages=[…], dispatch_thread_id="…", disposition="panel")`.
- **Strategic advice** → `dispatch(tool="advisor", …)` [overflow]. **Named pipeline** → `pipeline(op=run|async)`.
⚠ A build harness is not a model picker: "want a grok answer" → `team_dispatch(op=generate, role=artisan, model="xai/grok-4.3")`.
Full shapes + wrap/contract semantics + executor tiers: agent-skills/consult-routing.md → claude-web-dispatch-decision-table.md."""

# Co-located liveness block (2a durable home). Trimmed per F4-A finding (thread
# 1289): 3-question redirect + salience line kept inline; substrate table collapsed
# to prose — reference-density detail lives in git-posture skill (entity agent_skill:git-posture).
# inject-channel block key: entity-hierarchy-block
_ENTITY_HIERARCHY_BLOCK = """\
## Entity granularity — seed the right type
- **Todos have steps; plans have phases** — invariant. `phase`/`Phase N` is plan-domain only (`plan:` / `plan_phase:` / `/implement-plan`); on `todo:`/`task:` use **steps**/**slices**, never phases.
- **work item** = canonical genus for `project:`/`plan:`/`task:`/`todo:`. plan→plan_phase children (phases); task→todo children via `child_of` + umbrella `project:` via `related_to` (bounded arc of ≥2 leaf todos ordered by steps/`depends_on`); todo→steps inline in the body (¬ cram "PHASE 1/2/3" — that's a plan).
Seed via `entity_create` + `relationship_create child_of`; refs: agent-skills/entity-lifecycle-discipline.md."""

# inject-channel block key: liveness-block
_LIVENESS_BLOCK = """\
## Git posture & liveness — disk + cortex canonical; git ≠ project index
A change is LIVE only when LOADED into the running process at its last deploy/restart — git commit/master is neither necessary nor sufficient. Before claiming a surface changed, ask: (1) WHICH substrate? (2) did its LOAD EVENT fire? (3) what does the LIVE PROBE say? — service behavior→`sync_restart`+observability · MCP surface→mcp restart+boot manifest · routing→`/v1/models` · agent-context→`cortex_boot`. ¬ infer existence/canonicality/done-ness from git; commit is NOT a completion gate.
Coding-session detail: `agent_skill:git-posture` → `docs/agent-guides/skills/git-posture.md` (md_list / md_read). Tag one-liner: `[universal:git-posture]` in injected `architecture-invariants`."""

# Compact index — full playbook is agent-skills/consult-routing.md (current superset,
# verified 2026-06-04). The two highest-frequency traps are kept inline; everything
# else defers to the skill. See F2 finding, thread 1289.
# inject-channel block key: consult-routing-gate-block
_CONSULT_ROUTING_GATE = """\
## Consult routing — read the skill before dispatching
On any consult / review / second-opinion / handoff / dispatch outside this seat, read `agent-skills/consult-routing.md` BEFORE choosing transport (full playbook; this block is only the index).
Mandatory preflight before ANY handoff packet or `team_dispatch(op=handoff)` (implement NOT exempt):
  fs(cortex, agent-skills/consult-routing.md)
  fs(workspaces, .cursor/rules/architecture-handoff-protocol.mdc)   # § Six Blocks
  fs(workspaces, .cursor/rules/handoff-dispatchers.mdc)             # § target seat
Codified bug ticket = TWO phases (investigate→dense spec, then execute); a filed bug defaults to the INVESTIGATION tier (friction 13571 → thread 1377). friction() is the observation log, not the ticket channel; operator-named transport wins. Full model: consult-routing.md § Codified bug reports."""

_RAG_SCOPE_AWARENESS_BLOCK = """\
## RAG corpus retrieval — primary tool (call directly; ¬ dispatch overflow)
Semantic corpus search from any seat (incl. Cursor): call the **primary** `rag` dispatcher directly — ¬ via `dispatch(tool="rag_search")` or `tool_search` overflow.
```
rag(op="search", arguments='{"query": "<natural language>", "scope": "<scope>", "limit": 20}')
rag(op="list_scopes")   # enumerate scopes before any absence claim
rag(op="coverage")      # per-scope indexed file counts
```
Default search is AUTO-SCOPED (LLM scope-classifier → predicted scope only), not corpus-wide. Before concluding "no prior art / not in the corpus": `list_scopes` then re-search with an explicit `scope=`. `pipeline_consult` is overflow + needs a prior `execution_id` (not ad-hoc lookup); `search_project_files` is regex/literal file search (`pattern=`)."""


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


# inject-channel block key: session-close-web-block
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
# This boot block is the card-level reminder; the full protocol body auto-injects
# via `agent_skill:model-tier-awareness-web` (UNIVERSAL/web, MUST_INLINE).
# inject-channel block key: tier-selection-block
_TIER_SELECTION_POINTER = """\
## Model tier — full protocol auto-injects
`agent_skill:model-tier-awareness-web` auto-injects on web boot (INJECT_REGISTRY / seat_preloaded). When the operator declares model identity or a task-class trigger fires, follow the **auto-injected** full protocol — do NOT re-derive tier rules from this pointer. Derived home: `agent-skills/model-tier-awareness-web.md`."""


def _tier_selection_orientation_for_agent(agent: str | None) -> str | None:
    if agent and agent.endswith("-web"):
        return f"\n{_TIER_SELECTION_POINTER}"
    return None


def _render_gates_strip(agent: str | None) -> str:
    body = _GATES_STRIP
    if agent and agent.endswith("-web"):
        body = f"{body}\n{_GATES_CAPABILITY_VERIFY_LINE}"
    return f"\n{body}"


# inject-channel block key: capability-verify-block
_SEAT_CAPABILITY_VERIFY_BLOCK = """\
## Seat capability verify — verification is shell-free on web (probe before refusing)
Absence of a shell ≠ a step is unavailable. Before ANY "this seat cannot run Y" claim, run `tool_search("Y")` and bind to the catalog row (deferred PRIMARY tools load by name; OVERFLOW tools run via `dispatch(tool="…")`).
- code gate → `dispatch(tool="quality_gate", arguments='{"files": ["path/a.py"]}')` (ruff + compileall + import-check; +Lane-A offline pytest when edits touch `libs/llm_adapters/` or `libs/model_id/`). Security replay (`http_replay`/`http_request`/`http_diff`/`session_store`/`js_analyze`) — call by EXACT name (¬ reliably keyworded in `tool_search`).
- **Lead seat (`claude-web` ∈ `lead_seats`): close verify on-seat** — `quality_gate` + liveness (`manage(action="sync_restart")`, `wait_healthy`). ¬ dispatch cursor for verify-only.
Arbitrary pytest paths (`services/rag/`, integration) + `tools/pipeline_test replay` are shell/CLI-only → hand off. Full catalog: .cursor/rules/handoff-dispatchers.mdc § Seat capability verify + agent-skills/consult-routing.md."""


def _seat_capability_verify_orientation_for_agent(agent: str | None) -> str | None:
    if agent and agent.endswith("-web"):
        return f"\n{_SEAT_CAPABILITY_VERIFY_BLOCK}"
    return None


# inject-channel block key: operator-posture-block
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
    domain: str | None = None,
) -> list[str]:
    """Return the capability-axis + liveness orientation blocks as card parts.

    All seats use the claude direct-call form (``team_dispatch`` in
    ``_PRIMARY_TOOLS`` direct-call form; ``panel_dispatch`` is primary on
    claude-web; overflow via ``dispatch(tool="…")`` for advisor/pipeline_consult).

    Default (``family is None``) renders the same form, matching the default
    ``(claude, cursor)`` seat.

    ``domain`` soft-reorders blocks (coding | life | mixed-minimal); bodies are
    never hard-suppressed except model-tier (pointer-only on web).

    Emitted above the skills list by ``render_briefing_card()``. Each element
    carries a leading newline so the card's ``"\\n".join(parts)`` produces a
    blank-line separator consistent with the other sections.
    """
    session_close_block = _session_close_orientation_for_agent(agent)
    tier_selection_block = _tier_selection_orientation_for_agent(agent)
    capability_verify_block = _seat_capability_verify_orientation_for_agent(agent)
    domain_key = (domain or "mixed-minimal").strip().lower()
    core_blocks = [
        f"\n{_OPERATOR_POSTURE_BLOCK}",
        f"\n{_MCP_BINDING_LIVENESS_BLOCK}",
        _render_server_primary_manifest_line(),
        f"\n{_DISPATCH_CONSULT_BLOCK_CLAUDE}",
        f"\n{_CONSULT_ROUTING_GATE}",
        f"\n{_RAG_SCOPE_AWARENESS_BLOCK}",
        f"\n{_LIVENESS_BLOCK}",
        f"\n{_ENTITY_HIERARCHY_BLOCK}",
    ]
    if domain_key == "coding":
        order = [1, 3, 4, 0, 2, 5, 6, 7]
    elif domain_key == "life":
        order = [0, 4, 1, 2, 3, 5, 6, 7]
    else:
        order = list(range(len(core_blocks)))
    blocks = [_render_gates_strip(agent)] + [core_blocks[i] for i in order]
    if session_close_block:
        insert_at = 4 if domain_key == "life" else 3
        blocks.insert(insert_at, session_close_block)
    if tier_selection_block:
        blocks.insert(1, tier_selection_block)
    if capability_verify_block:
        blocks.insert(2, capability_verify_block)
    return blocks


def orientation_block_skill_map() -> dict[str, tuple[str, ...]]:
    """Shared-lib inject-channel map (drift-trap re-export for renderer binding)."""
    return ORIENTATION_BLOCK_SKILL_MAP
