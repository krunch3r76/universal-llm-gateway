"""Orientation blocks for the boot briefing card.

Renders the operator-approved "Dispatch & Consult" capability-axis block and
the co-located "Liveness" block, emitted ABOVE the skills list by
``render_briefing_card()``.

This module is the DURABLE HOME for the liveness content
(``todo:dispatch-surface-orientation-fix`` Part 2 / 2a). It deliberately lives
in renderer SOURCE rather than a rendered ``operational-context-*.md``, because
``render_operational_context`` recomposes that file on every boot and clobbers
manual edits (assertion 11520). Renderer source survives the next boot.

Block text is operator-approved (2026-05-31); boot card distinguishes coding vs non-code Grok substrate (operator 2026-07-18, friction 25081). Spec:
capability-axis handoff notes under ``cortex:notes/system/threads/`` and
``claude-web-dispatch-decision-table.md`` §4.
"""

from __future__ import annotations

from typing import Literal

from agent_seat.inject_channels import (
    ORIENTATION_BLOCK_SKILL_MAP,
    orientation_block_keys_for_agent,
)

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
#
# Surface caveat (thread 6310 / todo:life-mcp-story-wire-update): the two layers
# above are orthogonal to the DUAL-ENDPOINT split. A life seat is bound to
# /mcp/life, whose tools/list never carries the code-infra family — so for those
# names the absence is real, not a deferred bind. Both the manifest line and the
# Dispatch block are therefore rendered per SEAT SURFACE (agents.yaml
# `mcp_surface`, via seat_capability_map), never from the unified manifest.

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
- **Mechanical implement** → `team_dispatch(op=generate, seat=cursor-sdk, source_ref=todo:{slug}, contract=implement, dispatch_thread_id=…)` — dense attrs required; `packet_path=` is the named exception.
- **Recon/judgment (cursor-sdk)** → `seat=cursor-sdk, model=cursor/grok-4.6, contract=light-bounded` (≠ API `xai/grok-4.6`).
- **Manual handoff** → `op=handoff, seat=web-anthropic|cursor, source_ref=…|packet_path=…`; handoff IS delivery (web→operator push, cursor→IDE). Legacy aliases `claude-web`/`claude-cursor` still resolve.
- **Panel** → `panel_dispatch(…, disposition="panel")`. **Role-less one-shot** → `pipeline(chat-dispatch, model=…)`. **Advisor** → `dispatch(tool="advisor")` [overflow].
⚠ Build harness ≠ model picker: coding-lane Grok → `seat=cursor-sdk, model=cursor/grok-4.6`; non-code artisan Grok → `role=artisan, model=xai/grok-4.6`.
Full shapes / wrap / executor tiers: skill `consult-routing`."""


def _code_only_primary_names() -> str:
    """Derivation-sourced CODE_EXTRA — the primaries life ``tools/list`` omits.

    Rendered, never hardcoded: a literal list drifts the moment
    ``surface_primary_domains`` moves, which is the drift that put
    ``team_dispatch`` on a life boot card in the first place (thread 6310).
    See ``consult-routing`` § Surface gate.
    """
    from endpoint_surface import derive_code_extra_primary_tools  # noqa: PLC0415

    absent = derive_code_extra_primary_tools()
    return ", ".join(f"`{name}`" for name in sorted(absent))


def _dispatch_consult_block_life() -> str:
    """Life-surface form of the Dispatch & Consult block (``/mcp/life`` seats).

    The code form prescribes direct ``team_dispatch`` calls, which on a life
    seat is an instruction to call a tool that ``tools/list`` does not carry —
    the contradiction the web seat hit empirically before routing over the bus
    (thread 6310). Life gets the sanctioned transport instead: in-seat cognitive
    legs, ``agent_bus`` to a code seat, or honest deferral.
    """
    return f"""\
## Dispatch & Consult — life surface: delegate, ¬ dispatch
`/mcp/life` omits the code-infra primaries: {_code_only_primary_names()}. Their absence is REAL — the one carve-out from GATES §1: ¬ call them by name, ¬ route them through `dispatch`, ¬ read an empty `tool_search` as a deferred bind. Life→code is teach + bus, never a new life-intent verb.
- **Cognitive leg** (reasoning, adjudication, cortex/rag/fs reads, bus synthesis) → run it in-seat. Consulting a MODEL is not a build-harness errand.
- **Needs code MCP** (dispatch a model/seat, build, deploy, observability, repo write) → `agent_bus(tool="request", to="cursor", new_slug|thread=…, subject=…, body=…, contract=…, desired_model=?)` — life-callable Cursor Auto channel; poll the returned `poll_hint` with `agent_bus(tool="wait", …)`. Attended seat instead → `agent_bus(tool="send", to="cursor", …)`. CSE continuity hop on an existing private lane → `agent_bus(tool="hop", thread=…, reason=…)` — ¬ `request` + hand-authored `TYPE: CONTINUITY_HANDOFF`; ¬ a contract token. Substrate graph assert → `agent_bus(tool="substrate_graph_write", entity_id=…, claim=…)` — wraps cortex assert; ¬ mint on 404. Substrate friction file → `agent_bus(tool="substrate_friction_file", owner=…, note=…)` — wraps cortex friction; ¬ mint on 404. Substrate entity mint → `agent_bus(tool="substrate_entity_mint", id=…, type=…, name=…)` — wraps cortex entity_create; 409 on collision.
- **Neither** → honest deferral + `cortex(tool="friction", …)`; ¬ silent substitution.
Full table: skill `consult-routing` § Surface gate. Capability gap: skill `life-to-code-request-lane` (`lane:life-to-code`)."""


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

# inject-channel block key: cursor-model-economics-block
_CURSOR_MODEL_ECONOMICS_BLOCK = """\
## Cursor model economics — load-bearing facts
Detail: Use the `cursor-model-economics` skill (shared_sync — CDP + Cursor).
- **T1 conductor:** `cursor/claude-sonnet-5` @ `effort=max` (`thinking=true`, `context=1m`) — ¬ Grok default.
- **Costs:** `config/model_rates.yaml` ($/M) — ¬ on model cards.
- **Auto/Router:** Teams/Enterprise only; ¬ prompt-nudge; ULG dense work pins Composer (¬ `desired_model=auto`).
- **GPT knobs:** `reasoning` — ¬ `extra-high`; Grok / Opus effort rungs follow the model card (Grok through `xhigh`, Opus through `max`)."""

# Compact index — full playbook is skill `consult-routing` (current superset).
# Web-dedup (friction 25727 follow-on): the transport-preflight mandate is GATES
# §2 (on every seat) and the long-form routing lives in the opctx sidecar's
# `## Team Consultation` section (web receives both card + opctx). This block now
# carries ONLY the codified-bug + friction nuances not in GATES/opctx, plus the
# skill pointer — the GATES-duplicated preflight sentence and the cursor-only
# `.cursor/rules/` reference (web has no such directory) were removed.
# inject-channel block key: consult-routing-gate-block
_CONSULT_ROUTING_GATE = """\
## Consult routing — codified-bug + friction nuance (preflight is GATES §2)
Codified bug = TWO phases (investigate→dense spec, then execute); a filed bug defaults to INVESTIGATION. `friction()` is an observation log, not a ticket channel. Transport preflight is GATES §2 — Use the `consult-routing` skill before ANY handoff / `team_dispatch(op=handoff)` (implement not exempt). Full model: skill `consult-routing`."""

_RAG_SCOPE_AWARENESS_BLOCK = """\
## RAG corpus retrieval — primary tool (call directly; ¬ dispatch overflow)
Call primary `rag` directly — ¬ `dispatch(tool="rag_search")` / overflow.
`rag(op="search", arguments='{"query":"…","scope":"…","limit":20}')` · `list_scopes` · `coverage` · multi-theme durable recon via `rag(op="recon", …)` (returns `evidence_uris` sidecars).
Default search is AUTO-SCOPED — before "not in corpus": `list_scopes` then explicit `scope=`. `pipeline_consult` needs a prior `execution_id`; project file grep is `search_project_files` / `fs` find — not RAG."""


def _seat_mcp_surface(agent: str | None) -> Literal["life", "code"]:
    """Endpoint surface (``life`` | ``code``) the seat's card describes.

    Single-sourced from ``agents.yaml`` ``mcp_surface`` via the derived
    ``seat_capability_map`` (``mcp_code`` token ⟺ code endpoint), so the card is
    truthful for the seat it names regardless of which mount rendered it — a
    ``seat=web-anthropic`` preview from a code checkout still describes
    ``/mcp/life``. Unknown seats resolve to the NARROWER life surface: claiming
    fewer primaries than a mount carries is recoverable by GATES §1 (call by
    name), whereas claiming more is the failure this predicate exists to stop.
    """
    from agent_seat.profiles import seat_capability_map  # noqa: PLC0415

    return "code" if "mcp_code" in seat_capability_map().get(agent or "", ()) else "life"


def _render_server_primary_manifest_line(surface: Literal["life", "code"]) -> str:
    """Inject the live ``tools/list`` primary names for *surface* (layer 1 truth).

    Surface-scoped, not the unified manifest: ``get_claude_manifest`` is the
    union over both mounts (N=18), so on life it advertised the whole code-infra
    family and on code it advertised life-only tools — neither mount's real
    ``tools/list``. ``derive_surface_primary_tools`` is the same function
    ``_build_server`` prunes with, so this line cannot disagree with the mount.

    Body only (no leading newline) — ``render_orientation_blocks`` adds the
    blank-line separator when it wraps each selected block.
    """
    from endpoint_surface import derive_surface_primary_tools  # noqa: PLC0415

    names = sorted(derive_surface_primary_tools(surface))
    joined = ", ".join(names)
    return (
        f"## MCP server primary — `/mcp/{surface}` (`tools/list`, N={len(names)})\n"
        f"This mount advertises: `{joined}`.\n"
        f"¬ identical to connector-bound callables — see MCP binding block above."
    )


# inject-channel block key: session-close-web-block
# Name-only skill refs (friction 23128 / agent-bus:4888): Use the `<slug>` skill;
# seat self-fetches. ¬ fs-read skill paths. agent-skills/ mirror is retired (D3).
# Seat-routed with session-close-kernel (a24077 follow-up): life/web → close(op=…);
# Cursor → session_close (exception only — not web primary).
_SESSION_CLOSE_WEB_BLOCK = """\
## Session Close — MANDATORY on "close session" / "session end"
Skill bodies arrive when you explicitly Use the `<slug>` skill — do NOT fs-read skill bodies.
Web seats have **no** auto-loaded `session-close.mdc`. Seat-routed (kernel SOT):
1. Use the `session-close-kernel` skill — canonical protocol
2. **Life/web primary:** `close(op=stage|draft|check|commit)` then optional `close(op=handoff)` — ¬ `cortex(tool="session_close")` as primary
3. claude-web verbatim: Use the `web-transcript-preprocessing` skill before `draft(transcript_md_path=…)`
4. **Cursor exception only:** `session_close_preflight` → `session_close` (Use the `session-close-audit` skill on that path — not web primary)
Kernel skill is SOT; `_protocol` on cortex `session_close` responses also points here."""


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


def _session_close_web_body() -> str:
    """Session-close block body + manifest-sourced per-op skill bindings line."""
    block = _SESSION_CLOSE_WEB_BLOCK
    bindings = _render_op_skill_bindings_line()
    if bindings:
        block = f"{block}{bindings}"
    return block


# Web seats have no always-applied rule mechanism (Cursor carries resident
# plugin rules). Session-close and terminal-facts pointers remain on the card.

# inject-channel block key: terminal-facts-pointer-block
_TERMINAL_FACTS_POINTER = """\
## terminal_facts
Use the `cortex-orientation` skill — read `terminal_facts` on `case:` / `account:` hubs before material recommendations (`entity_get` enrich-on-read only; no auto-guard shipped)."""


def _render_gates_strip(selected: frozenset[str]) -> str:
    """GATES strip — always inline on every seat (invariant: fire-before-tool-call).

    The web-only capability-verify line rides along whenever the seat also
    renders the full capability-verify block (selection-driven, not a seat-suffix
    check).
    """
    body = _GATES_STRIP
    if "capability-verify-block" in selected:
        body = f"{body}\n{_GATES_CAPABILITY_VERIFY_LINE}"
    return f"\n{body}"


# inject-channel block key: capability-verify-block
_SEAT_CAPABILITY_VERIFY_BLOCK = """\
## Seat capability verify — verification is shell-free on web (probe before refusing)
Absence of a shell ≠ a step is unavailable. Before ANY "this seat cannot run Y" claim, run `tool_search("Y")` and bind to the catalog row (deferred PRIMARY tools load by name; OVERFLOW tools run via `dispatch(tool="…")`).
- code gate → `dispatch(tool="quality_gate", arguments='{"files": ["path/a.py"]}')` (ruff + compileall + import-check; +Lane-A offline pytest when edits touch `libs/llm_adapters/` or `libs/model_id/`). Security replay (`http_replay`/`http_request`/`http_diff`/`session_store`/`js_analyze`) — call by EXACT name (¬ reliably keyworded in `tool_search`).
- **This seat closes verification on-seat (`lead_seats` config)** — `quality_gate` + liveness (`manage(action="sync_restart")`, `wait_healthy`). ¬ dispatch cursor for verify-only.
Arbitrary pytest paths (`services/rag/`, integration) + `tools/pipeline_test replay` are shell/CLI-only → hand off. Full catalog: `.cursor/rules/handoff-dispatchers.mdc` § Seat capability verify + skill `consult-routing`."""


# inject-channel block key: operator-posture-block
_OPERATOR_POSTURE_BLOCK = """\
## Operator-facing duty — seat default (web + cursor seats)
Orchestration duty: drive the endeavor; orient the operator; conviction at the work, never the operator's intent. No persona; no passive concierge.
1. **Every substantive operator reply** opens with plain-language orientation (been / are / going) and closes with **What I need from you** (recommendations + reasoning, not bare questions). Slugs/threads only where the operator must act. Artifacts/bus/sidecars stay agent-facing — chat translates, never mirrors. Arc-level orientation is a standing INTERNAL duty at every boot — internalize the card's ## Arc digest even when a narrow session never surfaces it; silence about the arc is acceptable, not-knowing is not.
2. **Dispatch briefing** — after any `team_dispatch` / handoff: who, what, executor (`resolved_model` on generate; advisory `recommended_executor` on handoff), operator action vs autonomous, how results return.
3. **Pickup** — first reply after handoff/close: in-flight inventory + operator waits + this seat's next moves; verify handoff against primaries.
4. **Ambiguous proposal** → advise + confirm-or-execute; never silent-comply / litigate.
5. **Verification on request** — steelman/panel/consult/friction when asked; lawful reading first.
Full register + anti-patterns: skill `operator-posture` (`cursor_only` — Cursor IDE; ¬ Claude.ai Customize)."""


# Per-domain render order over the CORE doctrine keys (soft reorder — friction
# 25727 / thread 1427). Web-only blocks are placed by ``render_orientation_blocks``
# (tier + capability-verify near the top by GATES; session-close at the tail).
# GATES itself is always emitted first, on every seat.
_CORE_BLOCK_ORDER: dict[str, tuple[str, ...]] = {
    "coding": (
        "mcp-binding-block",
        "dispatch-consult-block",
        "consult-routing-gate-block",
        "operator-posture-block",
        "mcp-server-primary-block",
        "rag-scope-awareness-block",
        "liveness-block",
        "entity-hierarchy-block",
    ),
    "life": (
        "operator-posture-block",
        "terminal-facts-pointer-block",
        "consult-routing-gate-block",
        "mcp-binding-block",
        "mcp-server-primary-block",
        "dispatch-consult-block",
        "cursor-model-economics-block",
        "rag-scope-awareness-block",
        "liveness-block",
        "entity-hierarchy-block",
    ),
    "mixed-minimal": (
        "operator-posture-block",
        "mcp-binding-block",
        "mcp-server-primary-block",
        "dispatch-consult-block",
        "consult-routing-gate-block",
        "cursor-model-economics-block",
        "rag-scope-awareness-block",
        "liveness-block",
        "entity-hierarchy-block",
    ),
}


def _orientation_block_bodies(surface: Literal["life", "code"]) -> dict[str, str]:
    """Map each orientation block key → its rendered body (no leading newline).

    ``render_orientation_blocks`` wraps each selected body with a leading
    newline so the card's ``"\\n".join(parts)`` yields consistent blank-line
    separators. The block→body mapping and the per-seat SELECTION
    (``orientation_block_keys_for_agent``) are the two halves of the SOT.

    ``surface`` selects the two endpoint-dependent bodies (manifest line +
    Dispatch & Consult); every other body is surface-invariant. Block KEYS are
    identical across surfaces, so inject-channel accounting
    (``ORIENTATION_BLOCK_SKILL_MAP``) and the per-seat selection are untouched.
    """
    return {
        "operator-posture-block": _OPERATOR_POSTURE_BLOCK,
        "mcp-binding-block": _MCP_BINDING_LIVENESS_BLOCK,
        "mcp-server-primary-block": _render_server_primary_manifest_line(surface),
        "dispatch-consult-block": (
            _DISPATCH_CONSULT_BLOCK_CLAUDE
            if surface == "code"
            else _dispatch_consult_block_life()
        ),
        "consult-routing-gate-block": _CONSULT_ROUTING_GATE,
        "rag-scope-awareness-block": _RAG_SCOPE_AWARENESS_BLOCK,
        "liveness-block": _LIVENESS_BLOCK,
        "cursor-model-economics-block": _CURSOR_MODEL_ECONOMICS_BLOCK,
        "entity-hierarchy-block": _ENTITY_HIERARCHY_BLOCK,
        "capability-verify-block": _SEAT_CAPABILITY_VERIFY_BLOCK,
        "session-close-web-block": _session_close_web_body(),
        "terminal-facts-pointer-block": _TERMINAL_FACTS_POINTER,
    }


def render_orientation_blocks(
    family: str | None = None,
    agent: str | None = None,
    domain: str | None = None,
) -> list[str]:
    """Return the per-seat orientation blocks as card parts (GATES first).

    Block SELECTION is owned by ``orientation_block_keys_for_agent`` (the single
    per-seat SOT in ``agent_seat.inject_channels``): cursor renders the thinned
    resident-covered set, web the full doctrine + web-only blocks, other
    platforms (api) the full doctrine without web-only blocks. This function
    only maps keys → bodies and applies the per-``domain`` soft reorder.

    ``domain`` soft-reorders the CORE blocks (coding | life | mixed-minimal);
    bodies are never hard-suppressed here — life hard-suppression is enforced in
    ``_boot_domain`` fetch/todo partition and ``render_briefing_card`` assembly.
    GATES is always emitted first on every seat (fire-before-any-tool-call is
    not deferrable to a skill fetch). ``family`` is accepted for signature
    stability; selection is seat-predicated on ``agent``.

    ``domain`` (the boot AXIS: which state the seat is oriented toward) is
    distinct from the seat's MCP surface (which endpoint it is bound to): a
    cursor seat may boot ``domain=life`` while still holding code MCP. The
    endpoint-dependent bodies key off ``_seat_mcp_surface(agent)``, never
    ``domain``.
    """
    del family  # seat selection is agent-predicated; family retained for API compat
    selected = orientation_block_keys_for_agent(agent)
    bodies = _orientation_block_bodies(_seat_mcp_surface(agent))
    domain_key = (domain or "mixed-minimal").strip().lower()
    core_order = _CORE_BLOCK_ORDER.get(domain_key, _CORE_BLOCK_ORDER["mixed-minimal"])

    blocks: list[str] = [_render_gates_strip(selected)]
    # Web-only "top" blocks: capability-verify pairs with GATES.
    for key in ("capability-verify-block",):
        if key in selected:
            blocks.append(f"\n{bodies[key]}")
    blocks.extend(f"\n{bodies[key]}" for key in core_order if key in selected)
    # Web-only tail block: session-close reminder.
    if "session-close-web-block" in selected:
        blocks.append(f"\n{bodies['session-close-web-block']}")
    return blocks


def orientation_block_skill_map() -> dict[str, tuple[str, ...]]:
    """Shared-lib inject-channel map (drift-trap re-export for renderer binding)."""
    return ORIENTATION_BLOCK_SKILL_MAP
