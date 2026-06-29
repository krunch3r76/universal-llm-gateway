# Phase 4 Summary: session-objective -> todo capture

**plan_phase**: `plan_phase:recon-lifecycle/phase-4`
**executor**: Claude Sonnet 4.6 (thinking, medium)
**date**: 2026-06-28

## Files modified

| Action | Path |
|---|---|
| MODIFY | `libs/cortex_store/dispatch_ops/ops_session_close.py` |
| CREATE | `libs/cortex_store/dispatch_ops/_session_objective_promote.py` |
| CREATE | `libs/cortex_store/dispatch_ops/test_objective_to_todo.py` |

Note: `_session_objective_promote.py` was created as a Cleanup extract —
`scripts/modularize scan` flagged `ops_session_close.py` at 563 SLOC (red);
the new helper was extracted per the Cleanup directive.

## Verification evidence

**compileall** (`python -m compileall -q libs/cortex_store/dispatch_ops/`):
```
(no output — clean)
```

**ruff check** (`ruff check libs/cortex_store/dispatch_ops/ops_session_close.py libs/cortex_store/dispatch_ops/_session_objective_promote.py libs/cortex_store/dispatch_ops/test_objective_to_todo.py`):
```
All checks passed!
```

**pytest** (`pytest libs/cortex_store/dispatch_ops/test_objective_to_todo.py -q`):
```
4 passed, 3 warnings in 1.58s
```

## What was implemented

1. **`_session_objective_promote.py`** (new) — `promote_session_objectives()`: opt-in
   helper that iterates `promote_todos` dicts, validates `slug`+`name`, derives
   `todo_id`/`source_uri`/`required_skills`, and calls `seed_recon_todo` (Phase 3
   helper) for each. Emits `cortex.session.objective.promoted` on any successful
   seeds. Returns `[]` for `None`/empty input; skips malformed specs silently.

2. **`ops_session_close.py`** (modified):
   - Added `promote_todos: list[dict[str, Any]] | None = None` param to
     `_op_session_close` (between `defer_gaps` and `dry_run`).
   - Added 4-line tail call to `promote_session_objectives(...)` at the end of
     `_op_session_close` (past all `dry_run`/error returns — promotion only runs
     on a successful real close). Merges `promoted_todos` into result additively.
   - Added import of `promote_session_objectives` from `_session_objective_promote`.

3. **`test_objective_to_todo.py`** (new, `@pytest.mark.offline`) — four hermetic tests
   using `migrated_db_path` + `_CORTEX_DB` monkeypatch:
   - `test_promote_objective_returns_created`: return is `[{"todo_created": "todo:obj-sample"}]`
   - `test_promoted_todo_attributes`: entity has `source_uri`, `required_skills`,
     `seed_contract_ack` set, `density_triage` absent
   - `test_promote_none_and_empty_are_noop`: both `None` and `[]` return `[]`
   - `test_malformed_spec_skipped`: spec with missing `name` is skipped silently

## Deviations

- **Cleanup extraction**: The spec's Cleanup section instructed extraction into
  `_session_objective_promote.py` if `modularize scan` flagged the file. The scan did
  flag it (563 SLOC pre-extraction), so the helper was extracted as directed. The
  public name is `promote_session_objectives` (no leading underscore — it's a module
  boundary public function). The call in `ops_session_close.py` uses the public name.

- **Test import**: Test imports `promote_session_objectives as _promote_session_objectives`
  from `_session_objective_promote` (not from `ops_session_close`) to match the actual
  code location after extraction.

- **`required_skills=[]` in test_malformed_spec_skipped**: The valid spec in this test
  includes `required_skills=["ulg-architecture"]` because the implement-lane shape gate
  rejects empty `required_skills` lists (422). The test exercises slug/name validation
  only, not missing-required_skills (which is a runtime gate, not a spec-parse skip).

## Open follow-ups

- Task 4 (MCP passthrough): confirmed no whitelist — `cortex()` dispatcher forwards
  `arguments` as `**kwargs` to `_op_session_close`. `promote_todos` binds by name;
  no MCP descriptor change required.
- Service rebuild (cortex-api rebuild + shadow-log `cortex.session.objective.promoted`)
  is coordinator-gated — not done here per phase scope.
- `ops_session_close.py` is still at 516 SLOC (red) after extraction — pre-existing
  debt in the multi-op module. Reducing it further is a follow-up refactor.
