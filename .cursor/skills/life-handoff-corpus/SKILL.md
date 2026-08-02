---
name: life-handoff-corpus
description: "On life/web-anthropic handoffs (/mcp/life) — prefer cortex-packaged corpus + ephemeral mirrors; workspaces readable when exploration is named. Binding for receivers and dispatchers."
related_skills: ["handoff-packet-authoring", "consult-routing", "agent-bus-discipline", "dispatch-workflow"]
---

# Life Handoff Corpus

Life-surface capability, corpus, and mirror contract for web-anthropic (`/mcp/life`).

**Authoring depth (dispatcher seat):** `handoff-packet-authoring` § Life-surface cortex-mirror gate + skill-inline gate + Block 5 life/code split. **Transport choice:** `consult-routing` (cursor/orchestrator).

## Core rules

`∀ life/web handoff: <mcp_capabilities> = (LIFE/CORTEX MCP: ON) ∧ (CODE/VORTEX MCP: OFF)`.

Default handoff plan: `cortex(entity_get/search)`, `agent_bus(fetch/reply)`, and `fs(sandbox="cortex", op="read")`. When the packet **encourages exploration**, also name `fs(sandbox="workspaces", op="read")`. Life-surface graph/write calls require explicit `<task_guidance>` or `<output_format>` authority. Code-only tools stay off life: `¬{team_dispatch, panel_dispatch, pipeline, manage, observability}`.

`¬ "You have MCP" ∧ ¬ "NONE"` — both collapse the life/code boundary and fail packet validation.

`∀ life/web handoff: prefer corpus ⊆ {cortex://, entity_ref, agent-bus:, inline excerpts}` — packaging yields fewer tool calls and a faster, more targeted response. `workspaces://` is **readable** on web (a:26424); do **not** claim otherwise. Name `workspaces://` explicitly when exploration is encouraged. Prefer `cortex://` for durability of artifacts that must outlive the session.

`∀ life/web packet|evidence|skill-inline sidecar: path ∈ cortex://ephemeral/handoffs/…` when packaging.

`¬ notes/system/threads/` as default consult mirror (that prefix = durable review/closeout sidecars).

`¬ dropbox/` for handoff mirrors (ingest/promote contract).

`¬ invent model= on handoff` to alter tools — handoff already forbids `model=`; the receiving surface defines capability.

## Capability split (binding — 2026-07-15; workspaces read sight a:26424)

| Surface | Available | Forbidden |
|---|---|---|
| Life / web-anthropic `op=handoff` | Life/Cortex reads and packet-authorized writes; agent-bus fetch/reply; `workspaces://` **read** when named | Code-only tools (team_dispatch, pipeline, manage, observability, …) |
| Code / Cursor MCP | Life capabilities plus full code/workspaces surface | Not granted to web-anthropic handoff by default |

Default authority is read/coordination. Do not infer graph walks or durable writes from MCP-on alone. Customize / non-code consults: no code skills; arch via skill-inline only when needed.

## Pointer scheme

| Receiver | Allowed corpus pointers |
|---|---|
| Life / web-anthropic (`/mcp/life`) | `cortex://`, entities (`todo:`, `decision:`, …), `agent-bus:N#turn-N` preferred; `workspaces://` readable — name when exploration is encouraged |
| Code / workspaces-capable MCP | `workspaces://`, `cortex://`, entities, `agent-bus:` |

Prefer packaging hot paths to `cortex://` even though `workspaces://` is readable — fewer tool calls, faster response. Prefer inlined excerpts when the exact evidence is small. Skill delivery remains skill-inline (full bodies / `allow_long_body`); ¬ satisfy skills via corpus pointers alone.

## Ephemeral mirror prefix

`todo:life-handoff-ephemeral-prefix` Option B. Server helper: `handoff_life_mirror.LIFE_HANDOFF_MIRROR_PREFIX`.

| Kind | Target |
|---|---|
| Handoff packet mirror | `cortex://ephemeral/handoffs/<thread-or-slug>-<stem>.md` |
| Checkout evidence to keep the packet lean | same prefix, pre-dispatch |
| Skill-inline sidecar | `cortex://ephemeral/handoffs/<id>-inlined-skills.md` |

Retention: ephemeral / manual reclaim in v1. Historical mirrors under `notes/system/threads/` stay; new writes use ephemeral.

Happy path: mirror **before** handoff so the life receiver can start from packaged corpus. Open-ended `workspaces://` browse is the exploration option, not the default.

## Receiver posture (web-anthropic / life) — advisory

1. Confirm Block 5 says life/Cortex ON and code/vortex OFF; reject an undifferentiated MCP grant.
2. Read the cortex mirror and named `cortex://` evidence through the life surface; use `workspaces://` when the packet names exploration.
3. Use the read/coordination default. Run graph walks or life-surface writes only when the packet explicitly requires them.
4. Inlined skill bodies on the thread / skill-inline blocks are binding for the consult.
5. Long findings: reply on the bus thread by default. Write a Cortex sidecar only when `<output_format>` explicitly requests it.

Dispatcher-named output paths may grant life/Cortex writes; they do not grant code-only tools.

## Friction anchors

a24222 · a24223 · a24408 · a24410 · a23964 · a23317 · a26424 · agent-bus:5143 · agent-bus:4986 · `todo:web-anthropic-handoff-mcp-off-default` · `todo:life-handoff-ephemeral-prefix`
