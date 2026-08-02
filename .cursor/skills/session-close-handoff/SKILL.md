---
trigger_match_terms: ["session-close-handoff", "session_close_handoff", "write", "handoff_prompt", "handoff_upsert", "session-boot-close", "writing", "session_handoff_upsert", "explicit-load", "discipline", "voice", "self-check."]
description: 'Before writing handoff_prompt or session_handoff_upsert — read for explicit-load discipline, voice, and self-check. Gate: operator_requested(S) ∧ in_flight_arc(S).'
---

# Session Close — Handoff

**Skill:** session-close-handoff · **Protocol rev:** 1.2  
**Gate:** `write_handoff(S) ⇒ operator_requested(S) ∧ in_flight_arc(S)` — read before `handoff_prompt`.  
**History:** `entity_get(agent_skill:session-close-handoff)` · `decision:transcript-scoped-handoff-explicit-load` (11583)

## Artifacts

| Field | Role |
|---|---|
| `summary` | ≥20 chars; journal row name |
| `session_summary_md` | structured Decisions / Open items / Domains |
| `handoff_prompt` | first-person voiced narrative for explicit retrieval only |

Handoff = durable retrospective + sometimes-live continuation. Forward half is a strong prior; verify against live graph before acting. **Close-mode matrix:** session_close handoff is the session-transport row of continuity-doc **close** mode — `handoff ≠ dispatch` and the depth gate above stand unchanged; adapters live at `cortex://notes/system/templates/continuity-doc.md` (agent-bus:5201 F3).

Kickoff sibling: fresh-session kickoff prompts are authored by skill `handoff-prompt-authoring` (7-part template). A kickoff Objective/Context/Gap may be seeded from a good `handoff_prompt`.

## Load discipline

```text
load_handoff(h) ⇒ explicit_transcript_ref(S)   -- boot omits
referenced(h) ≠ currently_relevant(h)
```

Load via `entity_get(transcript:…)` (`attributes.handoff_prompt`), journal row, or operator paste. Never auto-load at boot.

## Continuity efficiency (binding — operator 2026-07-20)

When the arc's continuity thread has **`spine=root`** (tag `role:root`, or CHECKPOINT legacy read), `handoff_prompt` is a **pointer into that continuity root** — not a paste of prior handoff bodies.

Required when `active_spine_root(S)`: root thread id · latest CHECKPOINT turn (or stale flag) · scoreboard URI if chartered · 1-line state · next. Chat carries the pointer; the next seat navigates the continuity root (`agent-bus-discipline` § Two CHECKPOINT modes — Continuity).

| Bad | Good |
|---|---|
| Paste prior `handoff_prompt` prose into the new handoff / chat | Cite root + CHECKPOINT turn; let the seat `get` that turn |
| Full tick-schema CHECKPOINT ceremony on a non-enrolled continuity root | Index-thin CHECKPOINT on continuity roots; full schema only when tagged `charter-runner` (tick-driven) |
| Leave finished worker windows open as “continuity” | Close `spine=work` worker threads; continuity root (or handoff pointer) is the spine |

Cross-ref: `agent-bus-discipline` § Standing root threads → Two CHECKPOINT modes (tick-driven vs continuity).

## Authoring rules

- At close: write only when operator requests “with handoff” ∧ arc is in flight. Routine complete arc ⇒ omit.
- Depth gate: `handoff_prompt ∨ handoff_source_path ⇒ transcript_depth ∈ {light, verbatim}`. Never `none` (server 422 `handoff.requires_transcript_entity`). Web/API: `light` + `session_summary_md`; no `transcript_md` required.
- Mechanic: pass optional `handoff_prompt` to `session_close`; post-close operator request ⇒ `cortex(tool="session_handoff_upsert", …)`.
- Retired paths: ¬ `rj_write(kind="handoff")`; ¬ auto-compose in closing reply unless requested.
- Operator-action inventory: every carried-forward in-flight dispatch MUST name awaited operator action: push web thread N / open IDE thread N + executor tier / nothing-seat-handles. Dispatch list without operator annotations is non-compliant.
- Inline verification: inline `handoff_prompt` auto-persists to `notes/system/handoffs/{session_id}.md` with `derivation=auto_persisted`. Server stamps transcript `handoff_verification={checks, passed, total}` for transcript anchor, cited-entity resolvability, annotated entity-state snapshot, optional `prompt_in_source`. “No confirmation writes” gates only when `passed < total`. Verified file-marker path uses `derivation=section` (not retired `file_markers`).
- `handoff_source_path` requires `handoff_source_section` or a filename that embeds `session_id` — otherwise marker extraction fails. Inline `handoff_prompt` is the default working path for non-code-fence handoffs.
- `handoff_prompt` must contain `transcript:{session_id}` or `notes/system/transcripts/{session_id}` verbatim, or the source path must embed `session_id`; otherwise close fails `handoff.missing_transcript_anchor`.

## Roadmap position in forward half

Required when:

```text
write_handoff(S) ∧ in_flight_arc(S) ∧ active_work_has_parent_container(S)
```

`active_work_has_parent_container` = active `todo:`/`task:` is child/leaf under `project:` or `task:`, or shares parent with ≥1 sibling work item. Standalone parent-less, sibling-less todo ⇒ omit roadmap block.

When required, include in order:
1. **Top-level container** — nearest enclosing `project:` or `task:`: id + name.
2. **Active position** — active `task:` and/or `todo:` id + name.
3. **Relevant siblings** — direct children of nearest parent, one hop. List non-terminal (`open`/`in_progress`/`blocked`) in full; collapse terminal (`done`/`cancelled`) to count. Do not traverse grandparent portfolio or unrelated projects.

For every listed item include:
- status token ∈ `{open, in_progress, done, blocked, deferred, cancelled, unknown}`;
- provenance marker `(live)` if read this session from `workflow_state`, else `(unknown)`.

Unread ⇒ `unknown`; never infer status. Bare name without status/provenance is non-compliant. Cheapest live read: `render_subgraph(root="{nearest_parent}", hops=1)` or `entity_get` per node. Verify at least container + active position; siblings may be `(unknown)` if unread.

Roadmap position ≠ Deferred inventory ≠ boot card. Roadmap = where work sits; Deferred inventory = dispatches + operator actions. Both may appear. Redundancy is justified only for cold-context paste-forward handoffs; keep scope one-hop.

## Deferred inventory

Name open threads/todos/artifacts as nouns — no dispatch imperatives. Each bus thread: thread id + disposition enum (`advise-close` | `closed` | `leave-open+reason`); spine=`root` uses the fuller block in `agent-bus-discipline` § Session-close thread disposition (derived from thin classification).

## Voice

First person, next-boot-me, reflective. Carry judgment/intent/framing the graph cannot hold; do not make a boot-card task list.

## Self-check

1. Operator requested? If no, omit.
2. First-person, next-boot-me?
3. Retrospective half true as of close?
4. Forward half framed as verify-before-trust?
5. Intent density beyond transcript?
6. `transcript_depth ≥ light` if handoff is set?
7. Every carried-forward dispatch annotated with operator action?
8. Nested active work includes roadmap position with status + `(live)`/`(unknown)` for every listed item?
9. If a spine=root continuity root exists: handoff is a pointer (root · CHECKPOINT turn · scoreboard · 1-line state · next) — ¬ paste of prior handoff bodies?

## Example (good; load-bearing)

> “I’m picking up the transcript-scoped-handoff arc. When the operator says ‘continue from transcript:cursor-…’, I’d read this — and still check the live graph before trusting my forward half.”
