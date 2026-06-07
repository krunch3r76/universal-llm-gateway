Run plan's verification checklist. Standalone or part of 6-step workflow.

**Load**: `@patterns_ws` `@core_ws` `@services_ws`

## Workspace Extensions

**Event-Driven Checks (MANDATORY for behavior changes)**:
- [ ] Event vocabulary covers new/changed behavior (signals + payloads)
- [ ] ∀ state transitions, decision points, concurrent boundaries: signal emitted
- [ ] State from events (∃! source) | ¬polling
- [ ] Event structure valid (see `patterns_ws.mdc`)

**Success Criteria Additions**:
- [ ] Event vocabulary complete for changed behavior

## Sequence

### 1. Locate
Find plan | Identify checklist | Locate expected files

### 2. Execute Checklist
- Run ∀ items | Execute ∀ test commands | Document PASS/FAIL | Never skip

### 3. Test Commands
Execute ∀ | Verify no errors | Check outputs | Document failures

### 4. Code Quality
- [ ] Compile: `python -m compileall -q {files}`
- [ ] Lint: `ruff check {files}`
- [ ] Imports: Resolve, ¬circular

### 4.5. SRP
- [ ] Handler >80 SLOC → split or justified
- [ ] Function ≥3 responsibilities → split

### 4.75. Legacy
- [ ] Deprecated removed | ¬dead code | ¬unused imports | ¬compat shims | Old APIs replaced

### 5. Integration
Test integration points | APIs work | Config applied

### 6. Report (optional)
Prompt: "Detailed report? (y/n)" → Full or brief summary

## Commands
```bash
# Compile
python -m compileall -q {files}

# Lint
ruff check {files}

# Legacy
ruff check --select F401 {files}
```

## Failure Format
```
FAILURE: {Item}
Command: {cmd}
Error: {msg}
Root Cause: {analysis}
Remediation: {steps}
```

## Success Criteria
- [ ] ∀ checklist PASS | ∀ commands succeed | Files exist | ¬lint/compile errors
- [ ] Legacy eliminated | Dead code removed | Clean phase transitions
