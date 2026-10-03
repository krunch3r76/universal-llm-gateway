Per-directory code overhaul: modularize, review, fix, docstring, verify.

**This command wraps a skill.** Machinery SOT: Use the `overhaul` skill
(§ When · § Posture · § Order · § Doc shape · § Accept the doc · § Forbidden).
This file is a thin wrapper — ¬ re-derive tiers, transports, or the architecture-doc shape here.

Headless / cursor-sdk / claude.ai enter by skill slug with no command layer.

## Root purpose

One directory becomes modular, reviewed, and documented for an agent reader first.
The architecture doc is a projection of declarations, stamped so a retrieved section
still carries its provenance.

## When

| Condition | Route |
|---|---|
| Operator `/overhaul {directory}` | This command → skill, gradual |
| Operator `/overhaul frontier {directory}` | Skill `frontier` column only |
| `scripts/modularize scan` red/yellow on a directory | This path |
| One oversized file blocks the directory | `/overhaul-file {file}`, then return here |
| Operator deferred the architecture doc | Skill stops before step 9 |

## Invocation

```
/overhaul {directory}
/overhaul frontier {directory}
```

`{directory}` is relative to the project root. `frontier` is the paid Stargate
override. Absent that word, the pass is gradual.

## Lead obligations

Load the skill and run its order.

1. Birth CHECKPOINT cites the credit-budget checklist before any split.
2. Production docstring scan is clean before a doc draft. Tests may remain.
3. The doc draft matches § Doc shape. An open synthesis marker without a named deferral rejects it.
4. One commit carries the directory and `docs/architecture/{subsystem}.md` together, after review.

## Skills

Use the `overhaul` skill (SOT) · `overhaul-program` · `docstring-quality` · `modularize-path` · `claude-ai-cdp-navigation` · `consult-routing`
