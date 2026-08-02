---
name: docstring-quality
description: "On any Python add/edit/dispatch — author docstrings to the quality bar, gate with docstring-quality, enhance only when thin; mandatory ULG code-work floor with architecture skills."
---

# Docstring Quality

## Invariant

```
∀ Python module|class|public_fn authored ∨ patched ∨ dispatched:
  docstring meets bar ≺ done_claim
∧ scripts/docstring-quality check|scan green on criticals
∧ thin ⇒ /docstring-enhance then re-gate
```

ULG code-work floor (with `architecture-invariants`, `ulg-architecture`,
`event-instrumentation-discipline`): load this skill before first write or
implement dispatch. Empty `required_skills` does not waive — Floor default-loads
the set.

## Ship gate (path-agnostic — BINDING)

Path-sim AC/R pins are **enrichment**, not the only coverage. Code that lands
outside `/path-sim` still owes the same ship gate:

```
∀ Python public-surface patch (any lane) ≺ done_claim:
  scripts/docstring-quality check|scan on touched files → criticals=0
  cite scan path + exit in closeout evidence
  concentrated warnings ∨ arch/RAG feedstock ⇒ /docstring-enhance (CDP) → re-scan
```

| Closer | Duty |
|---|---|
| `implement-todo` §5 / any `contract=implement` close | Scan `files_expected` (or touched `*.py`); criticals=0 before todo-close |
| light-bounded / in-seat / Task that mutates public Python | Same scan before PASS/done; ¬ waive because "not path-sim" |
| `/overhaul` | Own §5.5 / §5.6 / step-9 fail-closed (unchanged) |
| Lead path-sim closeout / R-after | Same scan — path-sim § Docstring AC |

**¬** invent API `role=reviewer|skeptic` docstring floors. **¬** skip scan when the
arc skipped path-sim.

Arch docs project **docstring inventory** (signatures/imports/docstrings), not
bodies. Thin docstrings ⇒ thin `docs/architecture/*.md` after `/overhaul`.

## Quality bar

| Scope | Min words | Must carry |
|---|---|---|
| Module | ≥15 | what · who calls · key invariants/design |
| Class | ≥15 | purpose · lifecycle · key methods |
| Public function | ≥10 | what (≠ name echo) · non-obvious params · return · side effects (events/I/O/mutation) |

**Forbidden:** empty · name-echo first sentence · restating the identifier only.

**Audiences (every docstring serves all three):**
1. Humans — why, not only what
2. Agents — callers, invariants, relationships
3. RAG embeddings — distinctive terms vs near-siblings

## Scope

| Skip | Require |
|---|---|
| Private helpers (`_name`) unless non-obvious | All public modules/classes/functions |
| `__init__.py` — brief re-export summary OK | New/changed public surface on any add |

## Gate (mandatory before done)

```bash
source ~/.venvs/universal/bin/activate
scripts/docstring-quality check {file}    # single file
scripts/docstring-quality scan {directory}
```

| Issue | Severity | Action |
|---|---|---|
| `empty` | critical (exit 1) | Author before proceed |
| `too_short` | warning | Improve if arch/RAG material would be thin |
| `name_echo` | warning | Rewrite first sentence to behavior/invariant |

## Remediation

When gate warnings stay concentrated or arch drafts stay weak:

```
/docstring-enhance {path}
```

Gradual default = **CDP Sonnet** (Claude subscription). Template:
`cortex://notes/system/templates/cdp-overhaul-docstring-enhance.md`.
Apply via `scripts/docstring-apply`; re-gate. **¬** Stargate API unless
`/docstring-enhance frontier` + operator cost approval.

¬ substitute enhance for write-time authorship on new code.

## Dispatch / packet

| Seat | Delivery |
|---|---|
| Cursor / cursor-sdk | `Use the docstring-quality skill` — self-fetch |
| web-anthropic | skill-inline / densify floor (slug alone fails) ∨ Customize Skills (`shared_sync`) |

Block 2 must cite this slug with the architecture pair on ULG code handoffs.

## Shape examples (rules-in-examples — keep L2)

**Module — good:** purpose + caller + invariant (≥15 words).  
**Module — bad:** `"""Request routing module."""` / `"""Handles routing."""`

**Class — good:** purpose + create/destroy + key methods.  
**Class — bad:** `"""Capacity tracker."""`

**Function — good:** normalize/resolve behavior + return/error.  
**Function — bad:** `"""Resolve model ID."""` / name-echo paraphrase.

## Related

`/overhaul` §5–5.6 · `/docstring-enhance` · `arch-docs-maintenance_ws` ·
`architecture-invariants` · `ulg-architecture` · `required-skills-pickup` ·
`path-sim` Stage-B (`skills=` + lead closeout criticals=0; CDP enhance when warnings starve feedstock) ·
`path-sim` § Docstring AC (R-admit challenges AC; R-after `/work-item-review` scans `files_expected`) ·
`implement-todo` §5 Ship gate (path-agnostic) · `cursor-sdk-instruction-standard` D3 public-Python row
