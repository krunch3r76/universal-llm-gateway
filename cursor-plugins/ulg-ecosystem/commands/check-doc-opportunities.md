Analyze uncommitted changes for documentation update opportunities.

**Workspace**: Load `@check-doc-opportunities_ws.mdc` if exists.

## Input
| Flag | Scope |
|------|-------|
| (none) | `git diff HEAD` (all uncommitted) |
| `--staged` | `git diff --cached` |
| `--unstaged` | `git diff` |

## Detection Patterns

| Type | grep Pattern | Required Update |
|------|-------------|-----------------|
| Bug fix | `fix\|bug` in commit/comments | Anti-patterns section |
| File move | `git diff --name-status \| grep "^R"` | Key files section |
| State machine | `state.*=\|StateMachine\|transition` | State machines section |
| Invariant | `Invariant\|∀\|∃\|⟹` | Invariants section |

## Commands
```bash
git diff --name-only HEAD          # Changed files
git diff HEAD | grep "^\+"         # Additions only
```

## Output Format

**Summary**: `Found {N} opportunities: {X} bugs, {Y} moves, ...`

**Per Finding**:
```
{TYPE}
   File: {path}:{line}
   → {doc path} → Section: {SECTION}
   → Add: {content}
```

**Checklist**:
```markdown
- [ ] {doc}: {section}: {description}
```

**None found**: "No documentation updates needed"
