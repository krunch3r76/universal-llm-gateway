---
trigger_match_terms: ["session-close-reflective-journal", "session_close_reflective_journal", "rj_write", "rj_consolidate", "shift", "session-boot-close", "reflection", "mid-session", "capture", "preferred", "consolidation", "handoff"]
description: Before rj_write(reflection) or rj_consolidate at shift — mid-session capture preferred; consolidation before handoff at close.
---

# Session Close — Reflective Journal

**Skill:** session-close-reflective-journal · **Protocol rev:** 1.0  
**Gate:** `reflect?(S)` — consider at close; prefer mid-session capture.  
**History:** `entity_get(agent_skill:session-close-reflective-journal)`

## Timing

```text
¬close_only(rj)   -- capture close-to-the-moment; close = final sweep
```

## Schema enums

| Field | Values |
|---|---|
| writeable `kind` | `entry`, `reflection`, `revision`, `consolidation` |
| retired `kind` | `handoff` — use `handoff_prompt` on close; ¬ `rj_write(kind="handoff")` |
| `link_type` | `contradicts`, `refines`, `supersedes`, `reopens`, `unresolved_with`, `continues`, `related`, `handoff_for` |

Handoff write path lives in `session-close-handoff.md`.

## Reflection — in-flight observation

Trigger: high-signal moment (~0–3/session): disclosure, aha, named tension, articulated pattern.

Shape: 2–5 paragraphs for next-boot-me; not audience performance.

Mechanic:

```text
cortex(tool="rj_write", {agent, register:"self", kind:"reflection", entry, session_id, links?})
```

## Consolidation — session shift (before→now)

Trigger when any holds: approach/belief shift; operator pushback landed; arc milestone; tension named; cross-session pattern.

Fields: `throughline`, `before`, `now`; optional `tension_points`, `rendered_shift`, `falsifier`, `source_entry_ids`.

Mechanic:

```text
cortex(tool="rj_consolidate", {agent, register:"self", entry, throughline, before, now, …})
```

Close order: consolidation before handoff when both are present.

## Anti-patterns

```text
¬(consolidation_without before→now contrast)
¬(skip_consolidation because session_was_technical ∧ shift_surfaced)
¬(reflection_at_close_only when shift_was_mid_session)
¬(consolidation_disguised_as_handoff)
```
