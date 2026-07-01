<!-- target:* -->
# Engineering Conduct

## Logging
**Invariant**: use the project's structured logger factory (`get_logger(__name__)`)
from the shared logging module. ¬ `import logging` / `logging.getLogger(...)`
directly. Level constants are re-exported from the shared logging module.

## Code Style
Naming: verbs (`calculate_x`), nouns (`pending_requests`), predicates
(`is_loaded`). Comments: "why" only. Symbols: ∀ ∃ ∃! ∈ ∉ ⊆ ∪ ∩ ∖ ∅ ∧ ∨ ¬ ⟹ ⟺

## Workflow
1. Events first (query the observability layer or events CLI); consider
   vocabulary updates when proposing changes
2. DELETE → IMPLEMENT → VERIFY: `ruff check --select=UP --fix && ruff format
   && compileall && ruff check`; tests changed → load the testing-discipline
   reference
<!-- /target:* -->
