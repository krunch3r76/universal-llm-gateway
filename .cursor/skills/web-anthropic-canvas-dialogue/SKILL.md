---
name: web-anthropic-canvas-dialogue
description: "When the operator wants Cursor chat→web-anthropic relay with Canvas replies — rewrite to agent-bus + Jupiter nudge; refresh .canvas.tsx. Cross-window/satellite with ULG path+MCP."
trigger_match_terms:
  [
    "web-anthropic-canvas-dialogue",
    "life-advisor-canvas",
    "canvas dialogue",
    "relay to web-anthropic",
    "life advisor panel",
    "type in chat render canvas",
  ]
related_skills:
  [
    "claude-ai-cdp-navigation",
    "agent-bus-discipline",
    "operator-posture",
  ]
---

# Web-anthropic Canvas Dialogue

Operator pattern: **type in Cursor chat → seat relays to web-anthropic → reply renders in a Canvas beside chat.**

Not a live website. Canvas cannot `fetch` / poll agent-bus. The seat **rewrites the `.canvas.tsx` file** whenever a bus turn lands.

## When to load

- Operator asks for a canvas / panel / side view of life-advisor or web-anthropic dialogue
- Cross-window or satellite Cursor seat should continue an existing relay without inventing a new UX
- Resuming after CHECKPOINT that names a life-advisor canvas path

## Invariant

```
operator_types_in_chat
  ⇒ seat_rewrites_to_agent_bus(web-anthropic)
  ∧ seat_jupiter_nudges(project-ask)   # unless CDP already mid-nudge for same thread
  ∧ seat_refreshes_canvas(embed_turns)
¬ canvas_input_fields
¬ canvas_fetch(agent_bus)
```

## Default artifacts

| Artifact | Path / id |
|---|---|
| Canvas | `~/.cursor/projects/<workspace>/canvases/life-advisor-dialogue.canvas.tsx` (or endeavor-named twin) |
| Bus side channel | Prefer a dedicated life thread (e.g. **5242**); ¬ dump relay traffic onto endeavor root CHECKPOINT thread |
| Continuity SoT | Endeavor continuity card when BOE/Chase (e.g. `5222-endeavor-continuity.md`) |

Satellite / other-window seats: same canvas filename under **that** window's `canvases/` dir, or copy the pattern. Bus thread id is shared (Cortex/agent-bus), not the canvas file.

## Per-turn recipe

1. **Hear** operator concern in chat (plain language).
2. **Rewrite** for web-anthropic: short bus body + optional sidecar; cite continuity / thread ids; ask for lived/directive reply (not entity dump) when life-advisor mode.
3. **`agent_bus` send** (`thread=` continue ∨ `new_slug=` for new concern arc).
4. **Jupiter nudge** via `claude-ai-sync-jupiter project-ask --intent opus --converse …` — Use the `claude-ai-cdp-navigation` skill. Soft batch ≤3 turns when queueing multiple nudges (§ Converse batching — soft ceiling).
5. **Poll** bus for web reply; when landed, **rewrite canvas** embedding: operator ask · cursor rewrite · web reply (and later turns if continuing).
6. **Orient operator** briefly in chat; detail lives on canvas.

## Canvas content contract

- Embed turns **inline** (no network).
- Show: How-this-works one-liner · status pills · Your concern · Rewritten ask · Web reply (omit section until reply exists — ¬ empty placeholder cards).
- Update `updatedAt` + thread id in META on every refresh.
- Import only from `cursor/canvas`. Use the `canvas` skill for SDK tokens / Pill sizes.

## Abort / lane hygiene

Aborting a nudge: **Stop the Cowork stream first**, then release the CDP/python holder. Kill-only is forbidden (friction 24838). Detail: `claude-ai-cdp-navigation` § Abort in-flight Cowork.

## Anti-patterns

| Bad | Good |
|---|---|
| Put a text box in the canvas for the operator to type | Operator types in chat only |
| Expect canvas to auto-poll bus | Seat refreshes canvas on each landed turn |
| One Jupiter session per micro-nudge when 2–3 are queued | Soft-batch ≤3 `--prompt-file` turns |
| Relay on endeavor root CHECKPOINT thread | Dedicated life side channel |
| Kill CDP holder while Stop is still showing | Stop stream → then free lane |

## Portability note (`_ulg` pending)

This skill is for any Cursor seat with **vortex MCP + access to the ULG tree** (hub or satellite). Filename/scope suffix (`_ws` vs `_ulg`) tracked on `todo:ulg-suffix-satellite-skill-scope` — do not block use on that rename.
