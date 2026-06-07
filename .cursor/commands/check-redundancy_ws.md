# Check Redundancy

**Invariant**: ∀ logic_pattern, ¬∃ duplicate_implementation

Run before: utilities, validators, error handlers, transformations, shared components.

## Pre-Check
1. Semantic search for similar functionality
2. Check `libs/` for existing utilities
3. Review same directory/module
4. Verify uniqueness

## Detection
```bash
# Duplicate function patterns
rg "def (validate_|parse_|format_|normalize_|sanitize_|transform_)" --type py | sort | uniq -c | sort -rn

# Specific function
rg "def.*<name>" --type py

# Class patterns
rg "class.*(Handler|Manager|Processor|Validator)" --type py | sort | uniq -c

# Config parsing
rg "yaml\.load|json\.load|toml\.load" --type py -A 2

# Common imports
rg "^from.*import" --type py | sort | uniq -c | sort -rn | head -20
```

## Extraction Rules
| Scope | Location |
|-------|----------|
| Cross-service | `libs/` |
| Within-service | `service/utils/` |
| Domain-specific | Domain directory |

## Triggers
| Condition | Action |
|-----------|--------|
| Same logic 2+ places | Extract immediately |
| Similar with variations | Extract with params |
| Related scattered | Consolidate to domain |
| One-off | Keep inline |

## Anti-Patterns
❌ Copy-paste | ❌ Reimplement stdlib | ❌ Create when similar exists | ❌ Duplicate validation | ❌ Similar error handling
