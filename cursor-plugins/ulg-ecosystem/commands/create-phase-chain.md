Generate `phase{n}.md` sequence. Follows `create-implementation-plan.md` principles.

**Load**: `@patterns_ws` `@event-debugging_ws`

**Event Vocabulary**: ∀ phase touching behavior: include event vocabulary changes (new signals, updated payloads). Cross-subsystem phases: identify coordination signals at subsystem boundaries.

## When to Use
| Single Plan | Phase Chain |
|-------------|-------------|
| Mechanical refactoring | Complex >500 lines new logic |
| Atomic (¬intermediate breaks) | New logic across subsystems |
| Binary verification | High decision density |
| Low decision density | Context narrowing needed |

## Agent Instructions

### 1. Evaluate
Movement only → single | Intermediate breaks bad → single | Cross-subsystem → chain | >500 lines → chain

### 2. Create Structure
```
tmp/prompts/{project}/
├── phase1.md
├── phase2.md
└── summaries/
```

### 3. Generate

**Phase 1**: Foundation, reference workflow only
**Phase N>1**: Reference `summaries/phase{N-1}-summary.md`, can rewrite Phase N-1

## Phase Template
```markdown
# Phase {N}: {Title}

**Expected Executor**: {model}
**Executor Mode**: {thinking | non-thinking}
**Optional Consultation**: {model: question} or None
**Suggested Reviewer**: {model} or none

## Reference
- Previous: tmp/prompts/{project}/summaries/phase{N-1}-summary.md

## Objective
{May extend OR replace previous}

## Breaking Changes Allowed
- Delete/rewrite Phase {N-1} structures
- ¬backward compat | Intermediate non-functional OK

## Tasks
{create-implementation-plan.md standards}

## Verification
{Phase-specific, not full system}

## Expected Files
{This phase only}

## Next Phase Preview
{What comes next}
```

## Patterns
| Phases | Structure |
|--------|-----------|
| 2 | Core → Integration |
| 3 | Subsystem A → B (can rewrite A) → Integration |
| 4+ | Avoid (consider multiple features) |

## SRP Guardrails
∀ phase: include SRP split map | Split by responsibility before size

## Verification
```bash
for phase in tmp/prompts/{project}/phase*.md; do
  grep -q "^\*\*Expected Executor\*\*:" "$phase" || echo "❌ $phase"
  grep -q "^\*\*Executor Mode\*\*:" "$phase" || echo "❌ $phase"
done
```
