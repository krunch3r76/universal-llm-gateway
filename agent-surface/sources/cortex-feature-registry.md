<!-- target:* -->
# Cortex Feature Registry

## Invariant

**Invariant**: ∀ Cortex features: classified in the project's Cortex feature registry doc.

The registry maintains two views:
- **Foundation** — the Cortex foundation primitives with AGM coverage where applicable
- **Extensions** — lived capabilities beyond the foundation (staged by era)

## When Adding or Modifying Cortex Features

1. Check the Cortex feature registry for existing classification
2. **New feature**: add a row with origin, classification, implementation path, endpoints, MCP op
3. **Foundation change**: name the Cortex foundation primitive being extended and cite the relevant AGM postulate when the change affects revision semantics
4. **Extension**: document origin (era) and classification (retrieval / operational / coordination / infrastructure)
5. Update the registry in the same commit as the feature

## Classification Types

| Type | Scope |
|---|---|
| `retrieval` | Affects how information is found (search, activation, ranking, boot sections) |
| `operational` | Affects how information is managed (quality gates, enrichment, dedup, staging) |
| `coordination` | Affects multi-agent behavior (boot scoping, journaling, domain detection) |
| `infrastructure` | Affects system plumbing (storage, embeddings, migrations) |

## Anti-Patterns

| Bad | Good |
|---|---|
| New endpoint without registry entry | Add registry row in same commit |
| Extending a foundation primitive without naming the affected primitive | State which Cortex foundation primitive is changing and whether AGM semantics are affected |
| New MCP dispatch op without classification | Add to Extensions table with classification |

## Reference

- **Registry**: the project's Cortex feature registry doc (agent-facing, provenance-grouped)
- **Capability reference**: operational-lessons doc (agent-facing, purpose-grouped)
- **AGM compliance report**: canonical postulate-test report
<!-- /target:* -->
