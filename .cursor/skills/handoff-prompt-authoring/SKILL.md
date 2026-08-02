---
name: handoff-prompt-authoring
description: "Use when authoring a human-pasteable fresh-session kickoff or pickup prompt for a cold chat — operator paste or dispatch-turn prompt with no prior context."
applicable_agents: ["*"]
skill_category: dispatch-delegation
trigger_short: fresh-session kickoff ∨ pickup prompt ∨ 7-part handoff ∨ response-format addendum ∨ dispatch-turn prompt
trigger_match_terms: ["handoff-prompt-authoring", "handoff_prompt_authoring", "kickoff prompt", "pickup prompt", "fresh-session", "paste-into-new-session", "7-part", "seven-part", "response-format addendum", "session compass", "dispatch-turn prompt"]
---

# Handoff-Prompt Authoring

Author iff next actor is a **cold session/model** with no context: operator paste into new chat, or dispatch-turn prompt.

| Artifact | Audience | Shape | Channel | Skill |
|---|---|---|---|---|
| 6-block packet | dispatched model | XML blocks | `team_dispatch` | handoff-packet-authoring |
| `handoff_prompt` field | next-boot-me/operator paste | 1st-person retrospective + forward | `session_close` | session-close-handoff |
| **fresh-session kickoff** | cold orchestrator | imperative 7-part | human paste | this |

Kickoff complements `handoff_prompt`: retrospective says where I left it; kickoff says go do X next. A kickoff may seed Objective/Context/Gap from a prior `handoff_prompt`. Do not duplicate session-close-handoff voice/gate/roadmap rules here.

## Seven-part kickoff template

Paste in order. Dropping a part forces cold-session reconstruction from memory.

| # | Part | Holds |
|---|---|---|
| 1 | **Tier declaration** | “You are running {family} {effort}” + seat role; fit-check/reasoning budget |
| 2 | **Boot + skill discovery** | `cortex_brief(...)`; native skill stubs + boot index |
| 3 | **Objective** | one focused goal + explicit scope and DEFERRED boundaries |
| 4 | **Context to pull LIVE** | named `entity_get`/`fs(read)` anchors: bound work item, frictions/decisions, skill sections, partial primitives; “read live, ¬ reconstruct” |
| 5 | **Gap/problem** | actual unsolved gap + concrete motivating failure |
| 6 | **Deliverable** | numbered concrete outputs; artifact + landing path + distill-attrs step |
| 7 | **Discipline** | tier/routing reminders, verify-before-assert, dogfood/mechanical residue route, closeout shape |

Part 4 anchor rule: every context line is a pull instruction, never prose summary. Include “read it live, don't reconstruct.” This prevents stale-prior failures and redundant re-dispatch/re-decision.

## Response-format addendum (operator-facing)

Append to operator-facing kickoffs:

- **Session compass:** every substantive reply opens `🎯 Objective: <phrase> | 📍 Now: <active track>`.
- **Acronym-expand on first use:** `RC-2 (Root Cause 2: …)` inline, every reply.
- **Emoji status markers:** ✅ done · 🔴 blocked · 🟡 in progress · 🔵 next · ⚠️ needs input.
- **Bold:** entity/assertion IDs, file paths, key decisions.
- **Emoji headers:** 🔍 Finding · 🚀 Dispatched · ⚖️ Decision · 📋 Tracks · ⚠️ Operator call.
- **Close:** `## 🎯 What I need from you` — ≤2 concrete items, or “Nothing — proceeding autonomously on X”.
- **Concision:** ≤1 mobile screenful; state intent once then execute.

## Dispatch-thread variant

For fixed-role receivers (`reviewer|skeptic|synthesizer|gatherer|artisan` or panel member), not a fresh orchestrator: drop Parts 1–2. Use condensed 4-part:

| # | Part | Holds |
|---|---|---|
| a | Objective + scope | one question + boundary |
| b | Corpus pointers LIVE | named anchors to read, not pasted prose |
| c | Specific questions + hard format | numbered asks + exact response shape |
| d | Output shape | closeout/finding contract |

Do NOT tell generated roles to “reply on this thread”; Stargate on-behalf delivery double-posts otherwise.

## Cursor-sdk analog

A 6-block packet’s `<task_guidance>` is the cursor-sdk kickoff prompt: same job, different transport. Load `cursor-sdk-instruction-standard` and satisfy D1 determinate steps, D2 constraint repetition, D3 mandatory self-check, D4 destructive-op hard-stop. Composer prompts must be dense and complete; under-specification is a routing error.

## Worked example pattern

```text
— TIER — You are running Opus Max. You are the ORCHESTRATOR; route only mechanical residue to cursor-sdk.
— BOOT — cortex_brief(...); native skill stubs + boot index.
— OBJECTIVE — Drive todo:X → done. SCOPE: this todo only. DEFER: named adjacent arcs.
— CONTEXT (LIVE) — entity_get todo:X; parent task + siblings; transcript:... handoff_prompt; fs/read named skills. Read live — do not reconstruct.
— GAP — Current standard/arc lacks X; concrete failure Y motivates it.
— DELIVERABLE — 1. artifact at path; 2. resolve fork; 3. cross-refs; 4. distill files_expected + acceptance_criteria.
— DISCIPLINE — Judgment on-seat; route mechanical residue; verify-before-assert; dogfood; close at depth=light.
```

Operator-facing prompts append the response-format addendum.

## Self-check

1. Tier declared (family + effort + seat role)?
2. Boot + native skill discovery wired (native discovery)?
3. Objective scoped; deferrals named?
4. Every context anchor is LIVE pull instruction, not summary?
5. Gap states concrete failure?
6. Deliverable names artifact + path + distill-attrs step?
7. Discipline names routing + verify-before-assert + closeout?
8. Operator-facing ⇒ response-format addendum appended?
9. Dispatch-turn ⇒ Parts 1–2 dropped, 4-part shape used, no “reply on this thread”?
