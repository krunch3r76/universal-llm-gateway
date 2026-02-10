# Event Naming Specification

## Overview

All events in Universal Stargate follow a **dot-notation, hierarchical naming scheme**.
Events represent **facts that occurred**, not functions or classes.

## Naming Format (Required)

```
<domain>.<subdomain>.<action>[.<qualifier>]
```

### Rules

| Rule | Requirement |
|------|-------------|
| Case | **lowercase only** |
| Separator | **dot-separated hierarchy** |
| Tense | **past tense** for completed facts |
| ❌ Forbidden | PascalCase, snake_case, type-like names |

### Domain Registry

| Domain | Purpose | Examples |
|--------|---------|----------|
| `telemetry` | Wire protocol messages | `telemetry.resource.updated`, `telemetry.model.loaded` |
| `federation` | Federation protocol | `federation.init`, `federation.auth.result` |
| `gateway` | Gateway control plane | `gateway.init`, `gateway.ping` |
| `model` | Model lifecycle events | `model.loaded`, `model.execution.completed` |
| `request` | Request lifecycle | `request.queued`, `request.completed` |
| `scheduler` | Routing and scheduling | `scheduler.routing.decided` |
| `resource` | Resource reservations | `resource.reserved`, `resource.released` |
| `monitoring` | Observability events | `monitoring.chat.completed` |
| `system` | System lifecycle | `system.started`, `system.shutdown` |

### Action Verbs (Past Tense)

| Category | Allowed Verbs |
|----------|--------------|
| State transitions | `started`, `completed`, `failed`, `changed` |
| Lifecycle | `loaded`, `unloaded`, `created`, `removed` |
| Operations | `queued`, `processed`, `routed`, `decided` |
| Resources | `reserved`, `released`, `updated` |

## Good vs Bad Examples

| ❌ Bad | ✅ Good | Reason |
|--------|---------|--------|
| `ModelInferenceCompleted` | `model.execution.completed` | PascalCase forbidden |
| `MODEL_INFERENCE_COMPLETED` | `model.execution.completed` | SCREAMING_SNAKE forbidden |
| `model_inference_completed` | `model.execution.completed` | snake_case forbidden |
| `InferenceComplete` | `model.execution.completed` | Use past tense |
| `GatewayAvailable` | `gateway.state.changed` | Events are facts, not states |

## Semantic Layering Principles

### Control Plane Events Must Be Workload-Agnostic

- "execution" ≠ "inference"
- Covers: LLMs, Whisper ASR, diffusion, batching, fine-tuning, pipelines
- Consumers must not need to understand *what* ran, only *that capacity was acquired or released*

### Batching-Aware Design

Events must remain valid when:
- One execution serves multiple requests (vLLM batching)
- One execution occupies multiple slots
- Partial completion exists

Future events may include:
```
model.execution.batch.started
model.execution.batch.completed
model.execution.slot.released
model.execution.capacity.changed
```

### Capacity Event Semantic Split

Two distinct signals for capacity changes:

| Signal | Purpose | `request_id` | Triggers Slot Release |
|--------|---------|--------------|----------------------|
| `model.execution.completed` | Request lifecycle completion | REQUIRED | Yes (via subscription) |
| `model.capacity.freed` | Wake-only hint | None | No |

**`model.execution.completed`**: Request-scoped slot release
- Emitted by request path on terminal outcomes (success, error, cancel)
- Contains `request_id` and `gateway_id` for slot tracking
- Triggers automatic slot release in `GatewayTracker` and `GateCapacityStrategy`
- One event per acquired slot

**`model.capacity.freed`**: Wake-only hint
- Emitted by Gateway WebSocket on MODEL_IDLE and MODEL_UNLOADED
- No `request_id` (not tied to a specific request)
- Wakes queue processors to re-check capacity
- Consumers: `MasterCapacityQueue`, `CapacityWaiter`

**Rationale**: Separates request-scoped lifecycle (slot release) from general capacity hints (wake-up).

## Event Registration

### Python Constant Convention

```python
# Constant: SCREAMING_SNAKE_CASE
# Signal string: dot.notation
MODEL_EXECUTION_COMPLETED = "model.execution.completed"
MODEL_CAPACITY_FREED = "model.capacity.freed"

@event_factory
def ModelExecutionCompleted(
    url: str,
    model_id: str,
    request_id: str,  # REQUIRED
    gateway_id: str,  # REQUIRED
) -> Event:
    """Create model.execution.completed event (request-scoped slot release)."""
    return Event(
        signal=MODEL_EXECUTION_COMPLETED,
        payload={
            "url": url,
            "model_id": model_id,
            "request_id": request_id,
            "gateway_id": gateway_id,
        }
    )

@event_factory
def ModelCapacityFreed(url: str, model_id: str) -> Event:
    """Create model.capacity.freed event (wake-only, no slot release)."""
    return Event(
        signal=MODEL_CAPACITY_FREED,
        payload={"url": url, "model_id": model_id}
    )
```

### Factory Naming Convention

Factory functions use PascalCase (Python convention for constructors),
but the signal string they emit uses dot.notation.

## Enforcement

Events are validated at:
1. **Static analysis**: `ruff` custom rule (future)
2. **Runtime**: EventBus rejects non-conforming signals

### Validation Pattern

```python
import re

EVENT_PATTERN = re.compile(r'^[a-z]+(\.[a-z]+){1,4}$')

def validate_event_signal(signal: str) -> bool:
    """Validate event signal follows dot-notation spec."""
    return bool(EVENT_PATTERN.match(signal))
```

## Migration Status

See `tmp/prompts/semantic-execution-rename/` for migration phases.
