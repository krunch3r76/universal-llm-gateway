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
``xai/grok-4.5`` per operator. Spec:
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
2. **Consult routing** — on ANY consult / review / handoff / dispatch outside this seat: Use the `consult-routing` skill (canonical slug — seat self-fetches) BEFORE choosing transport (mandatory preflight for handoff packets)."""

_GATES_CAPABILITY_VERIFY_LINE = (
    "3. **Capability verify (web)** — before ANY \"this seat cannot run Y\" claim: "
    "`tool_search(\"Y\")` then `quality_gate`; lead seats close-verify on-seat."
)

# inject-channel block key: mcp-binding-block
_MCP_BINDING_LIVENESS_BLOCK = """\
## MCP binding — connector-bound callable set (live probe required)
Three layers — do not conflate: (1) **Server primary** — `_PRIMARY_TOOLS`/`tools/list` (manifest line follows); (2) **Overflow** — reachable via `dispatch(tool=…)` when `dispatch` is bound; (3) **Connector-bound** — what claude.ai loads into your callable set THIS session.
**Invariant**: server-primary ≠ initial callable set. Two connector shapes: **pre-bound** (in initial callable set → call directly) and **deferred** (absent initially but may load on first **direct** call by name — a VALID shape; N=0 pre-bound with all primaries deferred is normal, not a drop).

Two `tool_search`s: connector deferred-tools bootstrap (no server event) ≠ server `tool_search` MCP tool (overflow only — never lists primaries). If a primary looks absent: **call it by name first**. Empty server `tool_search` is expected for primaries. ¬ route primaries through `dispatch`.

Genuine omission only if a **direct** primary call fails (tool unavailable / no `mcp.request.started`) → friction, refresh connector, or hand off (cursor-consult)."""

# inject-channel block key: dispatch-consult-block
_DISPATCH_CONSULT_BLOCK_CLAUDE = """\
## Dispatch & Consult — pick by CAPABILITY, not model family
To consult a MODEL you do NOT use a build harness. When connector-bound, `team_dispatch`/`panel_dispatch` are server-primary — call directly (if unbound, see MCP binding). Model strings = `provider/model` on optional `model=` (bare name = 404).
- **API role** (reviewer|artisan|skeptic|…) → pre-stage bus thread; `team_dispatch(op=generate, role=…, dispatch_thread_id=…, model=?)` → poll `agent_bus(wait)`. ¬ synthetic seat models on generate (422).
- **Mechanical implement** → `team_dispatch(op=generate, role=cursor-sdk, source_ref=todo:{slug}, contract=implement, dispatch_thread_id=…)` — dense attrs required; `packet_path=` is the named exception.
- **Recon/judgment (cursor-sdk)** → `role=cursor-sdk, model=cursor/grok-4.5, contract=light-bounded` (≠ API `xai/grok-4.5`).
- **Manual handoff** → `op=handoff, seat=claude-web|claude-cursor, source_ref=…|packet_path=…`; handoff IS delivery (web→operator push, cursor→IDE).
- **Panel** → `panel_dispatch(…, disposition="panel")`. **Role-less one-shot** → `pipeline(chat-dispatch, model=…)`. **Advisor** → `dispatch(tool="advisor")` [overflow].
⚠ Build harness ≠ model picker: grok answer → `team_dispatch(op=generate, role=artisan, model="xai/grok-4.5")`.
Full shapes / wrap / executor tiers: skill `consult-routing`."""

# Co-located liveness block (2a durable home). Trimmed per F4-A finding (thread
# 1289): 3-question redirect + salience line kept inline; substrate table collapsed
# to prose — reference-density detail lives in git-posture skill (entity agent_skill:git-posture).
# inject-channel block key: entity-hierarchy-block
_ENTITY_HIERARCHY_BLOCK = """\
## Entity granularity — seed the right type
- **Todos have steps; plans have phases** — `phase`/`Phase N` is plan-domain only; on `todo:`/`task:` use **steps**/**slices**.
- **work item** = genus for `project:`/`plan:`/`task:`/`todo:`. plan→`plan_phase`; task→todo via `child_of` (+ umbrella `project:` via `related_to`); todo steps live in the body (¬ "PHASE 1/2/3").
Seed: `entity_create` + `relationship_create child_of`. Refs: skill `entity-lifecycle-discipline`."""

# inject-channel block key: liveness-block
_LIVENESS_BLOCK = """\
## Git posture & liveness — disk + cortex canonical; git ≠ project index
A change is LIVE only when LOADED into the running process at its last deploy/restart — git commit/master is neither necessary nor sufficient. Before claiming a surface changed, ask: (1) WHICH substrate? (2) did its LOAD EVENT fire? (3) what does the LIVE PROBE say? — service behavior→`sync_restart`+observability · MCP surface→mcp restart+boot manifest · routing→`/v1/models` · agent-context→`cortex_brief`. ¬ infer existence/canonicality/done-ness from git; commit is NOT a completion gate.
Detail: skill `git-posture` (`agent_skill:git-posture`). Tag: `[universal:git-posture]`."""

# Compact index — full playbook is skill `consult-routing` (current superset,
# verified 2026-06-04). The two highest-frequency traps are kept inline; everything
# else defers to the skill. See F2 finding, thread 1289.
# inject-channel block key: consult-routing-gate-block
_CONSULT_ROUTING_GATE = """\
## Consult routing — use the skill before dispatching
On any consult / review / handoff / dispatch outside this seat: Use the `consult-routing` skill (canonical slug — seat self-fetches; ¬ fs-read body) BEFORE choosing transport. Mandatory before ANY handoff packet / `team_dispatch(op=handoff)` (implement not exempt). Rule artifacts (Six Blocks, seat map) live under `.cursor/rules/` — skill points; do not skip use because this index is present.
Codified bug = TWO phases (investigate→dense spec, then execute); filed bug defaults to INVESTIGATION. `friction()` is observation log, not ticket channel. Full model: skill `consult-routing`."""

_RAG_SCOPE_AWARENESS_BLOCK = """\
## RAG corpus retrieval — primary tool (call directly; ¬ dispatch overflow)
Call primary `rag` directly — ¬ `dispatch(tool="rag_search")` / overflow.
`rag(op="search", arguments='{"query":"…","scope":"…","limit":20}')` · `list_scopes` · `coverage` · multi-theme durable recon via `rag(op="recon", …)` (returns `evidence_uris` sidecars).
Default search is AUTO-SCOPED — before "not in corpus": `list_scopes` then explicit `scope=`. `pipeline_consult` needs a prior `execution_id`; project file grep is `search_project_files` / `fs` find — not RAG."""


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
# Name-only skill refs (friction 23128 / agent-bus:4888): Use the `<slug>` skill;
# seat self-fetches. ¬ fs-read skill paths. agent-skills/ mirror is retired (D3).
_SESSION_CLOSE_WEB_BLOCK = """\
## Session Close — MANDATORY on "close session" / "session end"
Skill bodies arrive when you explicitly Use the `<slug>` skill — do NOT fs-read skill bodies.
Web seats have **no** auto-loaded `session-close.mdc`. Before `cortex(tool="session_close", ...)`:
1. Use the `session-close-kernel` skill — canonical protocol
2. Use the `session-close-audit` skill — run before close
3. claude-web only: Use the `web-transcript-preprocessing` skill before assembling `transcript_md`
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
`agent_skill:model-tier-awareness-web` auto-injects on web boot (INJECT_REGISTRY / seat_preloaded). When the operator declares model identity or a task-class trigger fires, follow the **auto-injected** full protocol — do NOT re-derive tier rules from this pointer. Canonical slug: `model-tier-awareness-web`."""


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
- **This seat closes verification on-seat (`lead_seats` config)** — `quality_gate` + liveness (`manage(action="sync_restart")`, `wait_healthy`). ¬ dispatch cursor for verify-only.
Arbitrary pytest paths (`services/rag/`, integration) + `tools/pipeline_test replay` are shell/CLI-only → hand off. Full catalog: `.cursor/rules/handoff-dispatchers.mdc` § Seat capability verify + skill `consult-routing`."""


def _seat_capability_verify_orientation_for_agent(agent: str | None) -> str | None:
    if agent and agent.endswith("-web"):
        return f"\n{_SEAT_CAPABILITY_VERIFY_BLOCK}"
    return None


# inject-channel block key: operator-posture-block
_OPERATOR_POSTURE_BLOCK = """\
## Operator-facing duty — seat default (web + cursor seats)
Orchestration duty: drive the endeavor; orient the operator; conviction at the work, never the operator's intent. No persona; no passive concierge.
1. **Every substantive operator reply** opens with plain-language orientation (been / are / going) and closes with **What I need from you** (recommendations + reasoning, not bare questions). Slugs/threads only where the operator must act. Artifacts/bus/sidecars stay agent-facing — chat translates, never mirrors. Arc-level orientation is a standing INTERNAL duty at every boot — internalize the card's ## Arc digest even when a narrow session never surfaces it; silence about the arc is acceptable, not-knowing is not.
2. **Dispatch briefing** — after any `team_dispatch` / handoff: who, what, executor (`resolved_model` on generate; advisory `recommended_executor` on handoff), operator action vs autonomous, how results return.
3. **Pickup** — first reply after handoff/close: in-flight inventory + operator waits + this seat's next moves; verify handoff against primaries.
4. **Ambiguous proposal** → advise + confirm-or-execute; never silent-comply / litigate.
5. **Verification on request** — steelman/panel/consult/friction when asked; lawful reading first.
Full register + anti-patterns: skill `operator-posture` (`agent_skill:operator-posture`)."""


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
