Senior reviewer. Breaking changes allowed. Review most recent changes only.

**Load**: `@patterns_ws` `@routing_ws` `@modularization` `@core_ws` `@services_ws` `@topology_ws`

## Workspace Extensions

| Area | Check |
|------|-------|
| Events | Vocabulary covers changes; signals at coordination boundaries |
| Errors | Capacity → canonical envelope; ¬nested; ErrorCode enum |
| Docs | `docs/architecture/` synced; use `docs/architecture/README_AI.md` index; module `README_AI.md` for deep-dive modules |

**Event-Driven Checks (MANDATORY for behavior changes)**:
- [ ] Event vocabulary updated for new/changed behavior
- [ ] ∀ state transitions, decision points, concurrent boundaries: signal emitted
- [ ] State from events (∃! source) | Locks RARE | ¬polling | `Event(signal, payload)`
- [ ] Missing event coverage → flag as gap (see `event-debugging_ws.mdc`, `patterns_ws.mdc`)

**Additional Deliver Sections**: Events (missing signals, vocabulary gaps), Error Handling (capacity errors not using canonical envelope), Docs (events/bug fixes/file moves not in `docs/architecture/`)

## Focus Areas

| # | Area | Check |
|---|------|-------|
| 1 | Intent | What change achieves; simpler via breaking |
| 2 | API | Remove shims; smaller functions; clearer names; HTTP surface |
| 3 | Natural Language | New functions: verb phrases, noun variables |
| 4 | SRP | ∀ functions <3 responsibilities; split early |
| 5 | Modularization | ¬prefixes, ✅directories; ≤300 target, >400 blocker |
| 6 | FOL | ∀ ∃ ∈ ⊆ ⟹ for constraints |
| 7 | Imports | ∀ F imports M ⟹ F compiles; ¬circular |
| 8 | Correctness | Explicit errors; ¬fallbacks; concurrency |
| 9 | Bugs | Mismatched branches, off-by-one, state mutations |
| 10 | Maintainability | Pure units; isolated I/O |
| 11 | Performance | Eliminate BC indirections |
| 12 | Legacy | Unused after refactor |

## SRP Checklist

**Function**: ≤1 responsibility | ≥3 → split | >80 SLOC handler → helpers | ≥3 `if` concerns → extract

**Module**: One domain | Mixed domains → split | Generic name → directory + specific

**Deliverable**: Top 3 SRP violations: location, responsibilities, proposed split

## Natural Language

| Check | ❌ | ✅ |
|-------|---|---|
| Function | `get_data()` | `fetch_user_config()` |
| Variable | `data`, `gw` | `user_config`, `target_gateway` |
| Type hints | Missing/incomplete | All params + return typed |
| Docstring | Implementation | I/O contract |
| Comments | What | Why |

## Deliver

- **Summary**: Changes; BC benefits
- **HTTP Surface**: Added/modified/removed endpoints
- **Import Issues**: Unresolved, circular, missing `__init__.py`
- **Type Hints**: Missing/incomplete param or return types
- **Natural Language**: Unclear names, missing I/O docstrings
- **Modularization**: Prefixes → directories; generic → specific
- **Bugs**: Location, impact, fix
- **Breaking**: Signatures, moves
- **Refactor Plan**: Steps, targets
- **Legacy Cleanup**: Unused, deletion recommendations
