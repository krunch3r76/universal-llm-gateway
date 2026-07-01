<!-- target:* -->
# Documentation Patterns

## Principle: Source = Truth, README = Navigation

**README_AI.md** = Navigation + Architecture (≤150 SLOC)
**Source** = Specifications + Contracts + Invariants (single source of truth)

## README_AI.md Scope

| Include | Exclude (→ source) |
|---------|---------------------|
| Entry points, key files | Contract specifications |
| Data flow diagrams (ASCII) | API parameter details |
| Quick navigation (`rg` commands) | Invariant definitions |
| Version selection guidance | Handler implementation notes |
| Cross-cutting architecture | Error handling tables |

## Documentation Hierarchy

| Content | Location | Format |
|---------|----------|--------|
| Module purpose | `"""Module docstring"""` | Prose |
| Class contract | `class X: """..."""` | Docstring |
| Function behavior | `def f(): """..."""` | Docstring |
| Invariants | Inline near enforcement | Comment |
| Domain constants | Typed constant + docstring | `ALLOWED: list[str]` |
| Schemas | `.yaml` or typed dict | Inline comments |

## YAML Frontmatter
```yaml
---
component: <name>
type: ai-reference
version: 1.0
primary_files:
  - path/to/key.py
events_emitted: [EVENT_A]  # if event-driven
events_consumed: [EVENT_B]
---
```

## Sections (choose applicable)

### Core (all projects)
| Section | Content |
|---------|---------|
| `MODULE_STRUCTURE` | Dir tree + role table |
| `KEY_FILES` | Concern → file map |
| `ANTI_PATTERNS` | ❌ items with symptom/fix |
| `QUICK_NAVIGATION` | `rg` commands |

### Event-Driven
| Section | Content |
|---------|---------|
| `EVENT_EMISSION_POINTS` | Event → function → file |
| `EVENT_CONSUMPTION_POINTS` | Event → handler |

### Stateful
| Section | Content |
|---------|---------|
| `STATE_MACHINES` | State → transitions |
| `INVARIANTS` | ID, FOL, symptom, location |

## README_AI.md Template

```markdown
# {Component}

{One-sentence purpose}

## Entry Points
| Task | Start Here |
|------|------------|
| Understand flow | `core/orchestrator.py` |
| Add handler | `handlers/__init__.py` |

## Data Flow
{ASCII diagram - max 30 lines}

## Quick Navigation
- Handlers: `rg "class.*Handler" handlers/`
- Events: `rg "emit\(" --type py`
- Invariants: `rg "INVARIANT:" --type py`
```

## Update Triggers
| Change | Update |
|--------|--------|
| File moved/deleted | `MODULE_STRUCTURE`, `KEY_FILES`, YAML |
| Bug fix | `ANTI_PATTERNS` |
| New event emission | `EVENT_EMISSION_POINTS`, YAML |
| New invariant | `INVARIANTS` |

## Migration: README → Source

| README Section | Move To |
|----------------|---------|
| Contract specs | Dataclass docstrings |
| Invariants | Inline comments where enforced |
| Error handling | Exception class docstrings |
| Handler details | Handler module docstrings |
| Domain taxonomy | `domains.py` or constants module |

## Anti-patterns

❌ README duplicates source docstrings
❌ README specifies contracts (source = truth)
❌ README > 150 SLOC
❌ Invariants far from enforcement

✅ README navigates to source
✅ Source docstrings specify contracts
✅ Invariants near enforcement point

## Workflow
**Before**: Read README_AI.md, run QUICK_NAVIGATION
**After**: Update sections if emission/state/files changed
<!-- /target:* -->
