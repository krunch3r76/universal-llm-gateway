# Sticky routing and STICKY_CAPACITY errors

Sticky placement binds a model to a gateway for stability. When that gateway is
capacity-constrained and selection cannot proceed, master Stargate may wait (event-
driven eviction wait with optional starvation drain) and then return HTTP 503 with
`ErrorCode.STICKY_CAPACITY`.

## STICKY_CAPACITY error envelope

Responses use the standard protocol shape
`{code, message, source, retryable, data}` with `code=STICKY_CAPACITY` and
`retryable=true`.

### `message`

A one-line summary for OpenAI-shape clients that surface only `message` and `code`.
It names holder state on the bound gateway, eviction disposition, and waited time
versus the configured wait budget.

### `data` fields

| Field | Meaning |
|---|---|
| `model_id` | Routing key for the requested model |
| `gateway_id` | Bound gateway when known from the selection trace |
| `holders` | `loaded`, `busy`, and `loading` routing keys on the bound gateway, or `holders_unknown` with `holders_unknown_reason` |
| `eviction` | `disposition` ∈ `attempted`, `refused`, `not_applicable`, plus `constraint` and `reason` |
| `wait_budget_s` | Per-request `eviction_wait_timeout_s` (default 300s) |
| `waited_ms` | Milliseconds spent in the wait path before this raise |
| `budget_exhausted` | Whether the shared eviction wait budget is exhausted |
| `retry_guidance` | Operator-facing retry hint |
| `reason` | Site-specific failure reason when the raise path sets one (e.g. `capacity_exhausted`, `eviction_blocked_queue_timeout`) |
| `requeue_depth` / `requeue_max_depth` | Present on finalize requeue depth-cap failures |

Constraint-specific detail from feasibility (VRAM counts, gateway URL, etc.) is
merged into `data` when a capacity candidate exists on the trace.

## Raise sites

1. **No-selection rejection** (`no_selection_outcome.handle_selection_rejection`):
   sticky model with transient capacity failure after optional wait.
2. **Eviction requeue depth cap** (`eviction_requeue.requeue_after_transient_eviction`):
   nested requeue depth exceeded.
3. **Eviction requeue wait timeout** (same module): wait loop returned no gateway.

## Related signals

While waiting, Stargate emits `routing.eviction.wait.*` events including
`routing.eviction.wait.timeout` with `exit_reason`, `waited_ms`, and
`exit_constraint_summary`. Those fields inform `budget_exhausted` and eviction
disposition in the HTTP envelope but are not copied verbatim into `data`.