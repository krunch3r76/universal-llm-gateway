---
trigger_match_terms: ["walk graph", "walk_subgraph", "graph navigation", "hub entity", "employment history"]
trigger_short: "render_subgraph ∨ walk_subgraph ∨ session canvas"
skill_category: cortex-planning
description: Before calling render_subgraph, building a session-open canvas for a project/case/topic, or when render output is larger than expected — read for call shape and compactness posture.
---

# Subgraph Render

**Trigger:** before `cortex(tool="render_subgraph", ...)` or `cortex(tool="walk_subgraph", ...)`, when building a session-open canvas, or when output is larger than expected.

## Default choice

`orientation ∨ boot_hint ∨ hub_navigation ⇒ walk_subgraph` (lean topology; no assertion canvas).  
`need_assertion_canvas ⇒ render_subgraph`.

## Walk first — topology/orientation

```text
cortex(tool="walk_subgraph", arguments='{"root":"person:slug","hops":1,"edge_types":["related_to"]}')
```

| Param | Default | Guidance |
|---|---|---|
| `root` | required | `type:slug` |
| `hops` | 1 | 1–3 |
| `edge_types` | all structural | Narrow on hubs |
| `direction` | both | outbound / inbound / both |
| `entity_cap` | 200 | cheap; no Card v0 |
| `include_counts` | true | assertion + relationship counts |
| `promote_hubs` | true | include `summary_row` when `rel_count ≥ threshold` |

Typical hub target: structured envelope <10KB, markdown <8KB; not a hard cap.

## Render — assertion canvas

```text
cortex(tool="render_subgraph", arguments='{"root":"project:slug","hops":1,"top_k_assertions":10,"neighbor_fidelity":"depth_aware"}')
```

| Param | Default | Guidance |
|---|---|---|
| `root` | required | `type:slug` |
| `hops` | 1 | 1 for orientation; 2 for full project state |
| `top_k_assertions` | 7 | root only when `neighbor_fidelity=depth_aware` |
| `neighbor_fidelity` | `depth_aware` | `full` diagnostic · `depth_aware` default · `edges_only` delegates to walk |
| `hub_rel_threshold` | 20 | hub neighbors get `summary_row` + top-1 assertion |
| `edge_types` | all | filter to narrow scope |
| `include_superseded` | false | use with `full` for diagnostics |

Render hard cap: 50 entities (422 if exceeded). Walk cap: `entity_cap=200`.

## Compactness posture

`render_subgraph` reflects graph hygiene. If output is noisy: use default `neighbor_fidelity=depth_aware`, narrow `edge_types`, or switch to `walk_subgraph` when orientation is enough.

Hygiene diagnostic: `neighbor_fidelity=full ∧ top_k_assertions=50 ∧ include_superseded=true`.

## Size tiers — render markdown

| Size | Signal | Action |
|---|---|---|
| ≤5KB | healthy boot canvas | use directly |
| 5–10KB | acceptable task canvas | deliberate deep read okay |
| 10–20KB | noisy | narrow `edge_types` or fix upstream |
| >20KB | write discipline failed | fix graph or use `walk_subgraph` |

## Related skills

- `cortex-orientation.md` — calling convention, canonical ops
- `cortex-entity-restructure.md` — entity-bloat split protocol
