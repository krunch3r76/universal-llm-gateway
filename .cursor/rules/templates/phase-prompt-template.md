# Phase N: [Title]

**Expected Executor**: `[model-name]`

**Executor Mode**: `thinking`

**Optional Consultation**: `[model-name]` — [When to consult and success criteria]

**Suggested Reviewer Model**: `[model-name]`

## Reference Documents
- Workflow: `.cursor/rules/structured-implementation-workflow.md`
- Architecture: `[architecture-doc-path]`
- Previous Phase: `[phase-n-1-summary.md]` (if applicable)

## Objective

[Clear statement of what this phase accomplishes]

## Phase N Scope

**In scope:**
- [List of specific items]

**Out of scope:**
- [What's deferred to later phases]

## Breaking Changes Allowed

- [List allowed breaking changes]
- [What backward compatibility can be dropped]

## Cross-Phase Dependencies

### Dependencies on Previous Phases
- **Phase N-1**: [What this phase requires from previous phase]
- **Phase N-2**: [If applicable]

### Consumed By Later Phases
- **Phase N+1**: [What future phases will use from this phase]
- **Phase N+2**: [If applicable]

### Integration Points
- [Key interfaces/patterns that span phases]
- [Data structures shared across phases]
- [Extension points established]

### Extension Patterns Established
- [Patterns that future phases will follow]
- [Hooks for future functionality]

## Implementation Tasks

### Task N.1: [Task Title]

**File**: `path/to/file.py` (NEW/MODIFY, ~SLOC estimate)

[Implementation details]

## SRP Split Map

| Component | Responsibilities | SLOC Target |
|-----------|-----------------|-------------|
| [file.py] | [responsibilities] | ≤[target] |
| **Total Phase N** | **[description]** | **~[total]** |

## Verification Checklist

### Pre-Merge Validation (MANDATORY)

- [ ] **Compilation**: `python -m compileall -q [path]`
- [ ] **Import resolution**: [specific import tests]
- [ ] **Lint**: `ruff check [path]`
- [ ] **Sole maintainer**: [phase-specific checks]
- [ ] **SRP**: ∀ new files ≤[limit] SLOC

### Feature Checks

- [ ] [Phase-specific feature validations]

### Unit Test Sketch

```python
# Key test cases to validate phase functionality
```

## Success Criteria

- [ ] [Measurable success criteria]

## Expected Files

**Create**:
- `path/to/new/file.py`

**Modify**:
- `path/to/existing/file.py`

**Delete**:
- `path/to/obsolete/file.py`

## Next Phase Preview

**Phase N+1: [Title]** — [Brief description of what comes next]