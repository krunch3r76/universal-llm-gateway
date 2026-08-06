# /modularize — file → package directory

Split an oversized source file into a **module directory** (package-shadow) via a
work-item-seed–shaped cascade: **CDP architecture → Grok densify → Composer implement**.

**This command wraps a skill.** Machinery SOT: Use the `modularize-path` skill
(§ Root purpose · § Cascade · § Skill delivery floor · § Flows · § M0–M-Verify ·
§ Anti-patterns). This file is a thin wrapper — ¬ re-derive stages, poll recipes,
or packet tables here.

Headless / cursor-sdk / CDP enter by skill slug with no command layer.

## Root purpose (do not dilute)

| Before | After |
|---|---|
| `…/foo.py` (fat file) | `…/foo/` package + focused modules + `__init__.py` public surface |

SLOC/SRP are acceptance criteria for that split — not a substitute goal.

## When

| Condition | Route |
|---|---|
| Operator `/modularize …` | This command → skill |
| `scripts/modularize scan` red/yellow | This path |
| Editing a file that will exceed 400 SLOC | Flag → this path |
| Dense plan already in `tmp/modularize-plans/{name}.md` | Flow B (implement only) |
| State file `*-state.md` | Flow C (resume) |
| CDP/Stargate blocked; need a quick sketch only | Escape: `scripts/modularize plan {file}` (labeled — ¬ primary) |

## Invocation

```
/modularize {path/to/file.py}
/modularize tmp/modularize-plans/{name}.md
/modularize tmp/modularize-plans/{name}-state.md
```

## Lead obligations

Load the skill and run M0→M-Verify in order.

1. **Publish stage disposition** (FIRE/SKIP M-Arch + why) before densify when arch could fire.
2. **M-Arch admit-proof:** same turn as disposition claim — quote `execution_id` +
   `poll_hint` (or warm CSE followup admit) **or** honest halt. ¬ announce-only.
3. **Skill floor on M-Arch:** inline `architecture-invariants` + `modularize-discipline`
   (+ `ulg-architecture` for ULG `services/`/`libs/`). Fail closed if missing.
4. **Poll:** `agent_bus(wait)` + `poll_hint` for CDP and cursor-sdk — ¬ `pipeline(result)`
   as primary.
5. **Densify → approve → Composer** (omit `model=` on `seat=cursor-sdk`). Flow A keeps
   operator approval between densify audit and implement.
6. **Verify:** compileall · ruff · `scripts/modularize scan` on the new package; doc
   contract audit.

## Skills

Use the `modularize-path` skill (SOT) · `modularize-discipline` · `architecture-invariants`
· `ulg-architecture` · `consult-routing` · `claude-ai-cdp-navigation`
