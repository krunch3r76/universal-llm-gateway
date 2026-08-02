# Model lifecycle — deferred reference

Load when model loading, routing, coordinator throttling, or lifecycle subscriptions.

## Stargate authority

Stargate is sole authority for model load/unload. No external service decides when/where a model loads or stays resident.  
External services: one structural question — does model exist in catalog?  
Present ⇒ submit; Stargate loads on demand. Absent ⇒ structural failure; fail fast; do not retry.

Catalog presence: `GET /v1/models/{id}` `available` field, or aggregate `model.available` / `model.unavailable` events.

## Advisory lifecycle signals

Subscribers treat lifecycle signals as advisory. Missed/late signals ⇒ no state corruption or forward-progress block.  
∀ wait on lifecycle signal: timeout cap required. Ignore entire stream ⇒ same correctness, throughput may differ.

| Signal | Meaning | Reaction |
|---|---|---|
| `model.loading.started` | Cold-load window | Pause new submissions |
| `model.loaded` | Resident | Resume |
| `model.load.failed` | Cold-load failed | Restore optimism |
| `model.unloaded` / `worker.evicted` | Evicted | Pause; await `model.loaded` |
| `model.capacity.freed` | Wake hint | Re-check queue; do not release slots |

Keep internal Stargate states (LOADING, BUSY, ENGINE_DEAD, reservation machine) internal — ¬expose in external code, enums, or events.

Batch coordinators (RAG, fine-tune prep, bulk eval) may throttle on signals but must not override Stargate decisions.
