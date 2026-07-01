<!-- target:* -->
# Stargate Live State

## Invariant

**Invariant**: ∀ live model/node state queries: use Stargate API endpoints.
¬filesystem catalog, ¬config sticky overrides, ¬RAG config model fields. Those
are configuration inputs; the gateway synthesizes them with runtime telemetry
into its live state. Only the API reflects what is actually true right now.

## Eviction Model (CRITICAL)

**Invariant**: VRAM is managed dynamically. A model absent from a node's catalog
is the only hard barrier to placement. Current VRAM occupancy is not a placement
ceiling — the gateway evicts idle models to make room when a new model is requested.

- `vram_free_mb` = current free VRAM, not available capacity
- If a model's catalog entry exists on a node, the gateway can load it there,
  evicting idle models as needed
- ¬use `vram_free_mb` alone to conclude "not enough room"
- The right question: does the model have a catalog entry on that node?
  → `GET /api/v1/model-status/{id}` → `nodes[].node_id` shows where it's
  catalog-reachable (status `available` = in catalog but not loaded)

## Key Endpoints

| Endpoint | Use |
|---|---|
| `GET /api/v1/model-status` | All models — placement, load/busy/loading per node. Optional `?status=loaded` filter. |
| `GET /api/v1/model-status/{id}` | Single model — placement + per-node hardware: `parallel_slots`, `vram_mb`, `vram_free_mb`, `vram_total_mb`, `context_length` |
| `GET /api/v1/node-models` | Node-centric view — all models grouped by node |
| `GET /api/v1/gateways/status` | Gateway health: connected/enabled per node |
| `GET /api/v1/gateways/status/full` | Full gateway state: VRAM totals + model list per node |
| `GET /api/v1/admission/state?model_id=…` | Capacity pool state for one model: paused, loading, queue_depth |

## MCP Agent Access

Prefer MCP tools over raw curl:

| Tool | Covers |
|---|---|
| `model_status(model_id="…")` | Single model — placement + hardware. **Start here.** |
| `model_status()` | All models — summary placement view |
| `model_status(status_filter="loaded")` | Currently resident models only |
| `list_models(filter="local")` | Local model catalog IDs |
| `topology()` | **Compact system snapshot** — nodes (VRAM), loaded models (slots, placement). Best for orientation. |

## Anti-Patterns

| Bad | Good |
|---|---|
| Filesystem catalog scan for model availability | `model_status(model_id="…")` → check `nodes[].status` |
| Raw catalog file grep for hardware slots | `model_status(model_id="…")` → `hardware.<node>.parallel_slots` |
| `vram_free_mb < model_vram_mb` → "no room" conclusion | Check catalog entry exists; the gateway evicts to make room |
| Config sticky-overrides for routing decisions | `model_status(model_id="…")` → `summary.loaded_on` |
| SSH to node to check catalog | `GET /api/v1/node-models` or `topology()` |

## Routing Feasibility Tiers (Summary)

The gateway's routing engine uses three tiers per gateway:

| Tier | Condition |
|---|---|
| T0 (infeasible) | Unhealthy ∨ no catalog entry ∨ at capacity |
| T1 (preferred) | Loaded + has slot capacity ∨ not loaded + free VRAM |
| T2 (evict-load) | Not loaded + can evict idle model to make room |

T2 is automatic — agents never need to manually evict.
Catalog entry presence on a node is the gating condition, not current VRAM.

Full topology reference (physical roles, ports, SSH, federation): the
deployment-topology reference. Full lifecycle authority: the gateway is sole
authority for model load/unload — see the coordination-signal table in the
core architecture reference.
<!-- /target:* -->
