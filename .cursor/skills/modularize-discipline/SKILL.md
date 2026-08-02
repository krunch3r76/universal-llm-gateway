---
name: modularize-discipline
description: "On /modularize packet composition, file-split plans, or modularization audits — split-specific rules; pair with architecture-invariants and ulg-architecture."
trigger_match_terms: ["modularize-discipline", "modularize_discipline", "modularize", "file-split", "plan", "dispatch-delegation", "packet", "composition", "overhaul", "2.1", "web-claude", "deep-tier"]
---

# Modularize Discipline

Split-specific rules for file modularization plans and audits. Load with `architecture-invariants` and `ulg-architecture`; this skill does not restate universal transport/logging/topology rules.

## Load with

`composing_or_auditing_modularize_plan ⇒ load(architecture-invariants) ∧ load(ulg-architecture) ∧ load(implementation-plan-workflow) ∧ load(frontier-model-instructions) ∧ load(this)`.

A modularize plan is a single-phase implementation deck: executor must be able to execute from the plan without source reread except where explicitly instructed.

## Gates

- Read before composing `/modularize` or `/overhaul` §2.1 `<invariants>` blocks.
- Read before authoring any file-split plan.
- Read before auditing a modularize plan for names, logging, public surface, layout, or scope leaks.

## Modularization constraints

- **SLOC ceilings:** `new_file ≤ 300 SLOC ∧ existing_file ≤ 400 SLOC`. `scripts/modularize scan` is the gate.
- **Package-shadow layout:** splitting `foo.py` MUST produce `foo/` package + `foo/__init__.py` re-exporting the prior public surface. `¬ sibling_prefix_files(foo_a.py, foo_b.py, ...)`.
- **Forbidden names:** reject generic modules: `utils.py`, `helpers.py`, `common.py`, `misc.py`, vague `base.py`, `managers.py`. Use responsibility-specific names.
- **Public surface preservation:** `__init__.py` re-exports only consumer-imported public names, verified by grep. `internal_name.startswith("_") ⇒ ¬reexport` unless a consumer demonstrably imports it.
- **Docstrings:** every new module, public class, and public function gets a substantive responsibility/invariant docstring. Thin docstrings (`Helpers.`, `Routes requests.`) fail audit.
- **Change scope:** every changed line traces to the split or directly required cleanup. No unrelated refactors or opportunistic reformatting. Expected Files = boundary.
- **Logger replacement:** if source uses stdlib logging, migrate declarations to `from universal_logging import get_logger` and `logger = get_logger(__name__)`; do not replicate `logging.getLogger` into new modules.
- **Local validation:** dispatch output is proposal, not authority. Run plan audit + compile/lint/quality gates before accepting.

## Why package-shadow is mandatory

`foo.py → foo/` preserves import path and creates an encapsulation boundary. Sibling-prefix files break or complicate `from .foo import X`, leak internals into the parent namespace, and invalidate `__init__.py`-based public-surface/doc audits. Package-shadow also nests cleanly if a submodule later grows: `foo/output.py → foo/output/`.

## Packet composition

Reference this skill by canonical slug in `<invariants>`:

```text
agent_skill:modularize-discipline
```

Do not paste the legacy cache/stub file. The workspace stub `universal-llm-gateway/.cursor/invariants/modularize.md` redirects here.

## Audit anti-patterns

Reject and redispatch with a `<prior_pass>` block when any appears:

- Generic module names.
- Sibling-prefix layout instead of package-shadow.
- `logging.getLogger` replicated into new modules.
- Internal helpers re-exported without grep-proven consumer import.
- Opportunistic refactors or changed files outside Expected Files.
- Thin docstrings.
- Verbatim paste from old cache file instead of composed skill references.

## See also

- `implementation-plan-workflow` — plan deck density, BEFORE/AFTER completeness, phase checkpointing.
- `architecture-invariants` — universal transport/events/logging/type-hint invariants.
- `ulg-architecture` — ULG topology and service lifecycle.
- `/modularize` command: `universal-llm-gateway/.cursor/commands/modularize.md`.
- `/overhaul` command: `universal-llm-gateway/.cursor/commands/overhaul.md`.
- `architecture-handoff-protocol.mdc` — six-block packet contract.
