# Event-Driven State Architecture

**Version**: 1.0  
**Date**: December 12, 2025  
**Status**: Complete

---

## Overview

The Gateway → Stargate control plane uses pure event-driven architecture for all model state tracking. WebSocket provides real-time state updates with INIT message for bootstrap only.

### Core Principle

**Events are the single source of truth** for all dynamic model state. The INIT message provides point-in-time bootstrap state only, with real-time updates arriving exclusively via WebSocket events.

---

## State Ownership

All model state is owned by Gateway and synchronized to Stargate via WebSocket events.

| State | Owner | INIT Bootstrap | Event Updates | Stargate Attribute |
|-------|-------|---------------|---------------|-------------------|
| `loaded_models` | Gateway | ✅ Initial set | `MODEL_LOADED`, `MODEL_UNLOADED` | `_loaded_models` |
| `busy_models` | Gateway | ✅ Initial set | `MODEL_BUSY`, `MODEL_IDLE` | `_busy_models` |
| `loading_models` | Gateway | ❌ Empty | `MODEL_LOADING_STARTED`, `MODEL_LOADED`, `MODEL_LOAD_FAILED` | `_loading_models` |
| RAM/VRAM | Gateway | ✅ Initial values | `RESOURCE_UPDATE` | `_resources` |
| Catalog | Gateway | ✅ Full catalog | `CATALOG_UPDATE` | `_catalog` |

### State Initialization

**On WebSocket connection:**
1. Gateway sends INIT message with point-in-time snapshot
2. Stargate initializes local state from INIT data
3. Gateway begins streaming real-time events
4. Stargate updates local state via event handlers

**On reconnection:**
1. INIT message provides fresh snapshot
2. Stargate resets all state to INIT values
3. Event stream resumes with current state

---

## Event Flow Diagrams

### Inference Lifecycle

```
                                    Gateway                      Stargate
                                      │                             │
ResourceTracker.set_model_busy()     │                             │
        │                            │                             │
        ├─► emit INFERENCE_STARTED   │                             │
        │         │                  │                             │
        │         ├─────────────────►│ MODEL_BUSY ─────────────────►│
        │         │                  │                             │ _busy_models.add(model_id)
        │         │                  │                             │
        │   [inference running]      │                             │
        │         │                  │                             │
ResourceTracker.set_model_idle()     │                             │
        │                            │                             │
        ├─► emit INFERENCE_COMPLETED │                             │
        │         │                  │                             │
        │         ├─────────────────►│ MODEL_IDLE ─────────────────►│
        │         │                  │                             │ _busy_models.discard(model_id)
```

**Invariants:**
```
∀ model_id ∈ loaded_models:
    model_id ∈ busy_models ⟺ inference_active(model_id)

idle_models = loaded_models ∖ busy_models

∀ eviction_candidate ∈ idle_models:
    ¬inference_active(eviction_candidate)
```

### Model Loading Lifecycle

```
                                    Gateway                      Stargate
                                      │                             │
WorkerController.load_model()        │                             │
        │                            │                             │
        ├─► emit MODEL_LOADING_STARTED                             │
        │         │                  │                             │
        │         ├─────────────────►│ MODEL_LOADING_STARTED ──────►│
        │         │                  │                             │ _loading_models.add(model_id)
        │         │                  │                             │
        │   [loading...]             │                             │
        │         │                  │                             │
   [success]      │                  │                             │
        ├─► emit MODEL_LOADED        │                             │
        │         │                  │                             │
        │         ├─────────────────►│ MODEL_LOADED ───────────────►│
        │         │                  │                             │ _loaded_models.add(model_id)
        │         │                  │                             │ _loading_models.discard(model_id)
        │         │                  │                             │
   [failure]      │                  │                             │
        ├─► emit MODEL_LOAD_FAILED   │                             │
        │         │                  │                             │
        │         ├─────────────────►│ MODEL_LOAD_FAILED ──────────►│
                                     │                             │ _loading_models.discard(model_id)
```

**Invariants:**
```
∀ model_id:
    model_id ∈ loading_models ⟺ load_in_progress(model_id)

∀ model_id ∈ loading_models:
    model_id ∉ loaded_models

On reconnect:
    loading_models = ∅  (cleared, events will repopulate if needed)
```

### Model Unloading Lifecycle

```
                                    Gateway                      Stargate
                                      │                             │
WorkerController.unload_model()      │                             │
        │                            │                             │
        ├─► emit MODEL_UNLOADED      │                             │
        │         │                  │                             │
        │         ├─────────────────►│ MODEL_UNLOADED ─────────────►│
        │         │                  │                             │ _loaded_models.discard(model_id)
        │         │                  │                             │ _busy_models.discard(model_id)
```

---

## INIT Message Structure

The INIT message provides point-in-time bootstrap state when connection is established:

```python
{
    "type": "init",
    "data": {
        "version": "1.0.0",
        "gateway_name": "gpu-gateway-1",
        "models": ["model-a", "model-b"],  # Available models in catalog
        "loaded_models": ["model-a"],       # Currently loaded
        "catalog": {...},                    # Model metadata
        "resources": {
            "total_vram_mb": 24576,
            "available_vram_mb": 12000,
            "busy_models": ["model-a"]       # Currently busy (if any)
        }
    }
}
```

### INIT vs Events

| Aspect | INIT Message | Events |
|--------|-------------|--------|
| Timing | Connection establishment only | Continuous stream |
| Purpose | Bootstrap snapshot | Real-time updates |
| Frequency | Once per connection | Multiple per second |
| Staleness | Stale immediately | Always current |
| Loading state | ❌ Not included | ✅ Real-time tracking |

**After INIT, all state updates come through events.** The event-driven state (`_busy_models`, `_loading_models`, `_loaded_models`) is updated in real-time and remains accurate throughout the connection lifetime.

---

## Reconnection Behavior

On WebSocket reconnection:

1. **INIT message** provides fresh snapshot of current state
2. **Event-driven state** is reset to INIT values
3. **New events** update state in real-time from that point forward

### Implementation

```python
def _process_init(self, data: InitData) -> None:
    """Process INIT message and reset state."""
    # Reset to INIT snapshot
    self._loaded_models = set(init_data.loaded_models)
    self._busy_models = set(resources.busy_models)
    self._loading_models = set()  # Clear - reconnect means loading complete/failed
    
    # Update resources and catalog
    self._resources = resources
    self._catalog = catalog
```

**Rationale for clearing loading state:**
- Loading operations complete or fail during disconnection
- Gateway will re-emit `MODEL_LOADING_STARTED` if load is still in progress
- Prevents stale "loading" state for completed/failed operations

---

## Event Types Reference

### Model Lifecycle Events

| Event | Direction | Trigger | Stargate Handler | State Update |
|-------|-----------|---------|-----------------|-------------|
| `MODEL_LOADING_STARTED` | Gateway → Stargate | Load begins | `_handle_message()` | `_loading_models.add()` |
| `MODEL_LOADED` | Gateway → Stargate | Load succeeds | `_handle_message()` | `_loaded_models.add()`, `_loading_models.discard()` |
| `MODEL_LOAD_FAILED` | Gateway → Stargate | Load fails | `_handle_message()` | `_loading_models.discard()` |
| `MODEL_UNLOADED` | Gateway → Stargate | Unload completes | `_handle_message()` | `_loaded_models.discard()`, `_busy_models.discard()` |

### Inference Events

| Event | Direction | Trigger | Stargate Handler | State Update |
|-------|-----------|---------|-----------------|-------------|
| `MODEL_BUSY` | Gateway → Stargate | Inference starts | `_handle_message()` | `_busy_models.add()` |
| `MODEL_IDLE` | Gateway → Stargate | Inference completes | `_handle_message()` | `_busy_models.discard()` |

### Resource Events

| Event | Direction | Trigger | Stargate Handler | State Update |
|-------|-----------|---------|-----------------|-------------|
| `RESOURCE_UPDATE` | Gateway → Stargate | Resource change | `_handle_message()` | `_resources = new_data` |
| `CATALOG_UPDATE` | Gateway → Stargate | Catalog change | `_handle_message()` | `_catalog = new_data` |

---

## Implementation Details

### Gateway: Event Emission

**Location**: `services/_universal-llm-gateway/src/core/websocket/event_forwarder.py`

**Pattern**: EventBus → WebSocket message conversion

```python
FORWARDED_EVENTS = [
    MODEL_LOADED,
    MODEL_UNLOADED,
    MODEL_LOADING_STARTED,
    MODEL_LOAD_FAILED,
    INFERENCE_STARTED,      # → MODEL_BUSY
    INFERENCE_COMPLETED,    # → MODEL_IDLE
    RESOURCE_UPDATE,
    CATALOG_UPDATE,
]

def _event_to_message(self, event: Event) -> dict[str, Any] | None:
    """Convert EventBus event to WebSocket message."""
    if event.signal == MODEL_LOADING_STARTED:
        return create_model_loading_started_message(event.data["model_id"])
    elif event.signal == INFERENCE_STARTED:
        return create_model_busy_message(event.data["model_id"])
    # ... other events
```

### Stargate: Event Handling

**Location**: `services/universal-stargate/gateway_websocket/client.py`

**Pattern**: WebSocket message → local state update

```python
async def _handle_message(self, message: dict[str, Any]) -> None:
    """Handle incoming WebSocket message."""
    msg_type = message.get("type")
    data = message.get("data", {})
    
    if msg_type == MessageType.MODEL_LOADING_STARTED.value:
        model_id = data.get("model_id")
        if model_id:
            self._loading_models.add(model_id)
            logger.info(f"⏳ Model loading started on Gateway: {model_id}")
    
    elif msg_type == MessageType.MODEL_BUSY.value:
        model_id = data.get("model_id")
        if model_id:
            self._busy_models.add(model_id)
            logger.debug(f"⏳ Model busy on Gateway: {model_id}")
    
    # ... other events
```

---

## Design Principles

### 1. Single Source of Truth

**Gateway is authoritative** for all model state. Stargate maintains a synchronized read-only copy.

```
∀ state_key, ∃! update_path (Gateway owns state)
```

### 2. Event-Driven Updates

**No polling.** Stargate reacts exclusively to WebSocket events.

```python
# ❌ FORBIDDEN: Polling pattern
while True:
    status = await http_client.get("/status")
    await asyncio.sleep(1)

# ✅ CORRECT: Event-driven pattern
async def _handle_message(self, message):
    # React to event
    self._state.update(message.data)
```

### 3. INIT for Bootstrap Only

**Connection establishment** gets point-in-time snapshot. Runtime updates come via events.

```
INIT: loaded_models, busy_models (snapshot)
Events: Real-time updates (authoritative)
```

### 4. Fire-and-Forget Publishing

**Events published without blocking request path.** Uses `publish_async_nowait()` for non-blocking emission.

```python
# Gateway side
resource_tracker.set_model_busy(model_id)
# → Emits event (fire-and-forget)
# → Request path continues immediately
```

### 5. Graceful Degradation

**Disconnected Gateway = unhealthy Gateway.** No HTTP fallback for state synchronization.

```python
if not client.is_connected():
    # Gateway marked unhealthy
    # Not selected for routing
    return False
```

---

## State Access Patterns

### Stargate: Read-Only Access

```python
# Get current state (always up-to-date)
loaded = client.get_cached_loaded_models()      # frozenset
busy = client.get_cached_busy_models()          # frozenset
loading = client.get_cached_loading_models()    # frozenset

# Compute derived state
idle_models = loaded - busy
available_for_loading = catalog_models - loaded - loading
```

### Gateway: State Mutation

```python
# Only Gateway mutates state (via ResourceTracker, WorkerController)
resource_tracker.set_model_busy(model_id)
# → Emits INFERENCE_STARTED event
# → WebSocket broadcasts MODEL_BUSY message

worker_controller.load_model(model_id)
# → Emits MODEL_LOADING_STARTED event
# → WebSocket broadcasts MODEL_LOADING_STARTED message
```

---

## Verification & Testing

### Static Analysis

```bash
# Verify no defensive patterns remain
! grep -r "getattr.*_busy_models\|getattr.*_loading_models" services/
! grep -r "hasattr.*_busy_models\|hasattr.*_loading_models" services/

# Verify no HTTP fallback
! grep -r "http_fallback\|fallback.*http" services/

# Check for unused imports
ruff check --select F401 services/universal-stargate/gateway_websocket/
ruff check --select F401 services/_universal-llm-gateway/src/core/websocket/
```

### Runtime Testing

```bash
# Start services
./services/_universal-llm-gateway/scripts/start-gateway.sh debug &
./services/universal-stargate/scripts/start-stargate.sh debug &

# Load model (triggers MODEL_LOADING_STARTED, MODEL_LOADED)
curl -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3-8b-128k", "messages": [{"role": "user", "content": "Hi"}]}'

# Verify events in logs
grep -E "Model loading started|Model loaded|Model busy|Model idle" \
  /tmp/logs/universal-stargate/*.log
```

### Expected Log Output

```
[INFO] ⏳ Model loading started on Gateway: qwen3-8b-128k
[INFO] ✅ Model loaded on Gateway: qwen3-8b-128k
[DEBUG] ⏳ Model busy on Gateway: qwen3-8b-128k
[DEBUG] ✅ Model idle on Gateway: qwen3-8b-128k
```

---

## Migration History

### Phase 1: Critical Bug Fix (Dec 12, 2025)
- Added `_busy_models` attribute to `GatewayWebSocketClient`
- Initialized from INIT message snapshot
- Eliminated defensive `getattr`/`hasattr` patterns
- **Limitation**: Snapshot-only, became stale after connection

### Phase 2: Real-Time Busy/Idle Tracking (Dec 12, 2025)
- Added `MODEL_BUSY` and `MODEL_IDLE` WebSocket messages
- Wired `INFERENCE_STARTED` and `INFERENCE_COMPLETED` events
- Real-time updates to `_busy_models` via WebSocket
- **Result**: Always-accurate busy state for eviction routing

### Phase 3: Real-Time Loading Tracking (Dec 12, 2025)
- Added `MODEL_LOADING_STARTED` and `MODEL_LOAD_FAILED` WebSocket messages
- Added `_loading_models` attribute to `GatewayWebSocketClient`
- Real-time tracking of loading operations
- **Result**: Prevents double-loading, accurate loading state

### Phase 4: Architecture Cleanup & Documentation (Dec 12, 2025)
- Verified complete elimination of defensive patterns
- Documented pure event-driven architecture
- Created UML diagrams
- **Result**: Clean, documented event-driven system

---

## Future Considerations

### Potential Enhancements

1. **Event replay on reconnection**: Gateway could maintain event history buffer to replay missed events
2. **Event sequence numbers**: Detect missed events and request resync
3. **Partial state updates**: More granular RESOURCE_UPDATE events
4. **Event acknowledgment**: Ensure Stargate received critical events

### Non-Goals

- **HTTP fallback**: Pure WebSocket architecture is intentional
- **Stargate-side state mutation**: Gateway remains single source of truth
- **Polling**: Event-driven architecture eliminates need

---

## Summary

The event-driven state architecture provides:

✅ **Real-time accuracy**: Events keep state current  
✅ **Clean architecture**: No defensive patterns, no polling  
✅ **Single source of truth**: Gateway owns state, Stargate syncs  
✅ **Efficient**: WebSocket streams, fire-and-forget emission  
✅ **Reliable**: INIT bootstrap + event updates = always accurate  
✅ **Complete tracking**: Loaded, busy, and loading states  

**Invariant**: `∀ state_change, ∃ WebSocket event` (events are authoritative)

