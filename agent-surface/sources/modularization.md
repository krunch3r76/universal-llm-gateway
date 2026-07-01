<!-- target:* -->
# Modularization

## SLOC
| ≤300 | ✓ | 301-400 | ⚠ refactor | >400 | 🛑 BLOCKER |

Pre-edit: SLOC >350 ∧ adding >20 ⟹ split first
Check: `scripts/modularize scan <path>` (authoritative — excludes blank lines, `#` comments, and docstrings)
Quick estimate: `grep -cvE '^\s*(#|$)' <file>` (overcounts — includes docstrings; use script for accuracy)
Exempt: test files (`test_*.py`, `*_test.py`) — test size reflects coverage depth, not modularization debt.

## SRP
∀ fn: |concerns| ≥ 3 ⟹ split
∀ fn: (validation ∧ orchestration ∧ mutation ∧ I/O) ⟹ split
∀ class: SLOC >200 ⟹ extract module
∀ handler: SLOC >80 ⟹ split helpers

## Structure
∀ files: shared_prefix ⟹ create_directory
∀ name: name ∈ {utils,helpers,common,handlers,service,manager,core,base} ⟹ rename_specific
Dir: singular (`model/`), `__init__.py` <50 lines

## Domain Isolation
∀ domain: imports ⊆ {stdlib, third_party, self}
¬∃ (A, B): imports(A) ∩ types(B) ≠ ∅
Translation: higher-level domain only
DI: Protocol + factory, ¬direct_import

## Policy
∀ code: unused ⟹ DELETE
∀ pattern: |occurrences| ≥ 2 ⟹ extract_shared
Priority: Remove > Refactor > Add

## Pre-Edit SLOC Check

∀ file edit where SLOC >400 ∧ adding >20 lines:
1. Note that the file will exceed the 400 SLOC hard limit
2. Suggest: "Consider running `/modularize` on this file before adding more code"
3. Do NOT block the edit — just flag it

## Post-Implementation Check

∀ implementation session that created or modified files:
- If any modified file now exceeds 400 SLOC → flag for modularization
- If any new file was created above 300 SLOC → flag immediately

## Available Tooling

`scripts/modularize scan {path}` — find SLOC violations
`scripts/modularize plan {file}` — generate a refactoring plan via consultation
  and inject `.cursor/invariants/modularize.md` by default
`scripts/modularize plan {file} --invariants-file {path}` — override the
  prompt-facing invariant block for a special review context
`/modularize` command — full orchestrated workflow

## Default Dispatcher

∀ `/modularize` execution (step 3): default dispatcher is **`frontier-mcp`**
(`gpt-5.5`). ¬ local execution fallback — if Stargate is
unavailable, stop. `web-claude` is the explicit opt-in for multi-turn
interactive work.

## When NOT to Modularize

- ¬ split files in `pipelines.local/` (frozen legacy)
- ¬ split test files (test verbosity is acceptable)
- ¬ split pure-data files (event type registries, config schemas) below 600 SLOC
  — data files have weaker SRP pressure than logic files
<!-- /target:* -->
