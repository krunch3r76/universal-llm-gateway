<!-- target:* -->
# Testing Discipline

**Scope**: durable pytest in git. ¬ conflated with CI presence or `tmp/` scratch.

## Invariants

| ID | Statement |
|----|-----------|
| T1 | ∀ `test_*.py` / `*_test.py` ∈ repo ∖ `tmp/`: **durable contract** — absence of CI test workflow ¬⟹ impermanent |
| T2 | ∀ commit(test): runnable via `pytest <path>` on dev venv without live secrets/inbox/NFS |
| T3 | ∀ agent-authored test: ∃ **request** — user message ∨ phase/spec task ∨ migration deliverable ∨ review `BlockedReason: missing_test_coverage` |
| T4 | ∀ exploratory probe: path ∈ {`tmp/`, `scripts/dev/`} ∨ ¬tracked — default ¬commit |
| T5 | ∀ completion claim citing tests: quote pytest summary line (provenance discipline) |

## Agent test gate

```
¬(request) ⟹ ¬(create test file)
request ⟹ commit paired test when phase/spec lists it as deliverable
```

**Request** sources (closed): user; a phase/spec task; a paired migration/schema/registry test; handoff `missing_test_coverage`.

**¬ request** examples: typo/format-only; drive-by refactor; investigation with no spec task.

## Commit decision

| Commit | ¬ commit |
|--------|----------|
| Migration/schema/registry tests | One-off "does API work?" probes |
| Pure unit (in-memory DB, mocks) | Secrets, live external services, machine-specific paths |
| Phase deliverable + verify target | PII fixtures; orphan outside `pytest` discovery |

**Placement**: colocate under the package or service being tested — sibling to code under test.

## Verify (when T3 ∧ file changed)

1. Narrowest: `pytest <path> -q`
2. Quote summary in completion claim
3. Default repo gate remains lint + compile check — pytest additive

## Markers

∀ non-hermetic test: mark it (e.g. `@pytest.mark.integration`) + document manual run in the phase/spec until CI exists.

## Cross-refs

- `modularization.mdc` — test files SLOC-exempt
- `provenance-discipline.mdc` — test pass claims
- `bound-invariant-falsifier.mdc` — tracked `test_falsifier_*.py` tier (G10)
<!-- /target:* -->
