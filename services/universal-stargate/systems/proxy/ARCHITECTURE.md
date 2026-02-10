# Stargate Proxy - Refactored Architecture

## Overview

The Stargate proxy has been refactored into a clean, modular architecture with proper separation of concerns and elimination of race conditions. The key architectural principles are:

1. **`submit_chat_request`** - Prepares and submits requests
2. **`process_chat_completion`** - Executes requests when gateway is available
3. **Clear separation** between submission and execution
4. **StatusStore** - Single source of truth for gateway state (eliminates race conditions)
5. **Immutable snapshots** - Safe concurrent access to gateway status

## Architecture Components

### 1. **RequestPreparer** (`proxy/core/request_preparer.py`)

**Responsibility**: Transform and prepare incoming requests

**Key Features**:
- Parses and validates incoming requests
- Extracts user parameters
- Applies chat template transformations
- Handles token counting and management
- Prepares complete `RequestContext` for processing

**Main Method**:
```python
async def prepare_request(request, chat_request, model_override) -> RequestContext
```

Returns a `RequestContext` containing:
- Original request data
- Processed messages
- Transformation metadata
- Token metrics
- Request data ready for forwarding

### 2. **RequestExecutor** (`proxy/core/request_executor.py`)

**Responsibility**: Execute prepared requests and handle responses

**Key Features**:
- Forwards requests to gateway (streaming and non-streaming)
- Applies response transformations
- Handles monitoring and logging
- Supports both bypass mode and normal mode

**Main Method**:
```python
async def execute_request(context: RequestContext) -> Response
```

Takes a prepared `RequestContext` and returns the final `Response`.

### 3. **StatusStore** (`src/scheduling/status_store.py`)

**Responsibility**: Single source of truth for gateway state management

**Key Features**:
- Atomic, lock-guarded state operations
- Immutable snapshots for safe concurrent access
- Eliminates race conditions between monitoring and queuing
- All gateway state mutations go through this store

**Main Methods**:
```python
async def set_snapshot(url, snapshot) -> None
async def mark_gateway_busy(url, model_id) -> None
async def mark_gateway_available(url, model_id) -> None
async def get_all_snapshots() -> Dict[str, FrozenGatewaySnapshot]
```

**State Management**:
- All writes are atomic under `asyncio.Lock`
- Snapshots are immutable (`FrozenGatewaySnapshot`)
- No direct mutation of shared sets/lists
- Thread-safe concurrent access

### 4. **GatewayMonitor** (`src/scheduling/gateway_monitor.py`)

**Responsibility**: Fetch gateway data and emit snapshots (no direct state mutation)

**Key Features**:
- Only fetches data from gateway HTTP endpoints
- Creates immutable snapshots from API responses
- Pushes snapshots to StatusStore via `set_snapshot()`
- No direct mutation of shared state

**Main Methods**:
```python
async def _check_gateway_comprehensive(url) -> None
# Creates snapshot and calls status_store.set_snapshot()
```

**Data Flow**:
1. Fetch health and resource data from gateway
2. Create `FrozenGatewaySnapshot` from API response
3. Push snapshot to StatusStore atomically

### 5. **SimpleQueue** (`src/scheduling/simple_queue/queue.py`)

**Responsibility**: Manage asynchronous FIFO request queue for request coordination

**Key Features**:
- Stores full request contexts (not just model_id)
- FIFO ordering with asyncio.Queue
- Concurrency managed by gateway-level `try_reserve_slot()` (not per-model semaphores)
- Returns results to original callers via `asyncio.Future`
- **Always active** - no enable/disable configuration
- **Lock-free** design using asyncio primitives

**Main Methods**:
```python
async def enqueue_request(request_context, priority) -> QueuedRequestWithContext
# Returns a request that can be awaited for the result
```

**Processing Flow**:
1. Queue receives full `RequestContext`
2. Background task monitors queue
3. **Reads immutable snapshots** from StatusStore
4. When gateway available: load model → execute request
5. **All state changes** go through StatusStore API
6. Result returned via `Future` to original caller

### 6. **StargateProxy** (`proxy/stargate_core.py`)

**Responsibility**: Orchestrate request processing and wire components

**Key Features**:
- Simple, clean entry point
- Delegates preparation to `RequestPreparer`
- Delegates execution to either queue or direct processor
- Manages model loading for both paths
- **Wires StatusStore** into all components
- **Eliminates shared state** between components

**Main Flow**:
```python
async def submit_chat_request(request, chat_request, model_override):
    # 1. Prepare request with all transformations
    context = await self.request_preparer.prepare_request(...)
    
    # 2. Submit for processing
    if self.request_queue:
        return await self._enqueue_and_wait(context)
    else:
        return await self._execute_immediately(context)

async def process_chat_completion(context):
    # Called when gateway is available - does actual execution
    return await self.request_executor.execute_request(context)
```

## Request Cancellation

**Implemented**: 2026-01-31

Pipeline requests can be cancelled via `request_id`, removing waiters from queues and signaling remote workers.

### Cancellation Endpoint

```
POST /api/v1/pipeline/cancel
Content-Type: application/json

{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "model_id": "qwen2-5-7b-instruct"  // Optional
}
```

**Response**: `{"cancelled": true, "message": "Request cancelled successfully"}`

### Cancellation Flow

```
Cancel Request
  ↓
/api/v1/pipeline/cancel endpoint
  ↓
StargateProxy.cancel_request(request_id, model_id)
  ├─ Try queue cancellation
  │   ├─ MasterCapacityQueueManager.cancel_all_queues(request_id)
  │   ├─ StickyQueueManager.cancel(request_id, model_id)
  │   └─ NonStickyQueueManager.cancel(request_id, model_id)
  │
  └─ Try remote cancellation
      └─ MasterRequestTracker.cancel(request_id)
  ↓
Returns True if cancelled from any source
```

**Key Invariant**: `request_id = X-Request-ID || uuid4()`. Internal systems use
`request_id` only. Outbound federation sets `X-Request-ID = request_id`.

**Queue Cancellation Behavior**:

All queue types implement `cancel(request_id: str) -> bool`:

1. Find waiter by request_id under lock
2. Remove from queue
3. Set `asyncio.CancelledError` on waiter's future
4. Return True if found and cancelled

**Initiated From**:

- Pipeline map step timeouts: MapExecutor detects timed-out iterations, extracts map_iteration_request_ids, calls cancel for each
- Client disconnect: MapExecutor catches `CancelledError`, cancels all pending federation requests before propagating
- Manual cancellation: External tools can POST to cancel endpoint directly

## Request Flow

### With Queue System (Scheduler Enabled)

```
Client Request
    ↓
submit_chat_request()
    ↓
RequestPreparer.prepare_request()
    ├─ Parse & validate
    ├─ Apply transformations
    ├─ Token counting
    └─ Build RequestContext
    ↓
_enqueue_and_wait()
    ↓
SimpleQueue.enqueue_request()
    ├─ Add to queue
    └─ Return Future
    ↓
[Background Queue Processor]
    ├─ Find available gateway
    ├─ Load model (if needed)
    └─ Call process_chat_completion() ← ACTUAL PROCESSING
    ↓
process_chat_completion(context)
    ↓
RequestExecutor.execute_request()
    ├─ Forward to gateway
    ├─ Apply response transformations
    └─ Return Response
    ↓
Future.set_result(response)
    ↓
await Future → Response
    ↓
Client receives Response
```

### Without Queue System (Direct Processing)

```
Client Request
    ↓
submit_chat_request()
    ↓
RequestPreparer.prepare_request()
    ├─ Parse & validate
    ├─ Apply transformations
    └─ Build RequestContext (gateway instance not yet selected)
    ↓
_execute_immediately() OR _enqueue_and_wait()
    ↓
RequestExecutor.execute_request()
    ├─ _select_gateway_and_load_model() ← CRITICAL: Gateway routing + model loading
    │   ├─ Route to healthy gateway (1+ gateways)
    │   ├─ Trigger model load if not loaded
    │   ├─ Wait for model to be ready (up to 300s)
    │   └─ Store gateway_instance in context
    ├─ Token counting (now that model is loaded)
    ├─ Forward to selected gateway
    ├─ Apply response transformations
    └─ Return Response
    ↓
Client receives Response
```

## Race Condition Elimination

### **Problem Solved**
The original architecture had race conditions where multiple components (gateway monitor, request queue, request scheduler) were directly mutating shared sets like `loaded_models`, `busy_models`, etc. This caused "Set changed size during iteration" errors under concurrent load.

### **Solution: StatusStore Pattern**
1. **Single Source of Truth**: All gateway state lives in `StatusStore`
2. **Atomic Operations**: All mutations are lock-guarded and atomic
3. **Immutable Snapshots**: Consumers get `FrozenGatewaySnapshot` objects
4. **No Direct Mutation**: Components can't directly modify shared state

### **Data Flow**
```
GatewayMonitor → StatusStore → RequestQueue
     ↓              ↓                ↓
  Fetches data   Atomic updates   Read-only snapshots
  Creates       Lock-guarded      Never mutates
  snapshots     state changes     shared state
```

### **Concurrency Safety**
- **Monitor**: Only fetches data, creates snapshots, pushes to StatusStore
- **Queue/Scheduler**: Only reads snapshots, calls StatusStore API for mutations
- **StatusStore**: All mutations are atomic under `asyncio.Lock`
- **Snapshots**: Immutable `FrozenGatewaySnapshot` with `frozenset` collections

## Key Architectural Decisions

### 1. **Preparation Happens Once**

All request preparation logic is centralized in `RequestPreparer`. There's no duplication between queue and direct processing paths.

### 2. **Queue System Stores Full Context**

The queue doesn't just store `model_id` - it stores the complete `RequestContext` with all transformations already applied. This allows the queue processor to just load the model and execute.

### 3. **Async/Await for Results**

The queue system uses `asyncio.Future` to return results asynchronously. The original request handler awaits the future, making the flow clean and allowing concurrent request processing.

### 4. **Model Loading is Integrated**

Model loading logic (`_select_gateway_and_load_model`) is now consistently applied in both execution paths:
- **Normal mode**: Gateway selection + model loading happens before token counting
- **Bypass mode**: Gateway selection + model loading happens before request forwarding
- **All gateway configurations (1+)**: Unified API via ResourceAwareModelManager

### 5. **No 202 Responses**

Clients don't need to know about queuing. They just make a request and get a response, whether it was queued or processed immediately.

### 6. **Unified Model Management**

- **All gateway configurations**: Uses `ResourceAwareModelManager` for 1+ gateways
- **1 gateway**: ResourceAwareModelManager operates with one gateway (no routing needed)
- **2+ gateways**: ResourceAwareModelManager handles routing, load balancing, and failover
- **Unified architecture**: Same code path regardless of gateway count
- **Consistent API**: `ensure_model_loaded(model_id)` → `GatewayInstance`
- **Automatic fallback**: If no model manager is available, logs warning but continues (graceful degradation)

### 7. **Elimination of Shared State**

- **Before**: Multiple components directly mutated `GatewayStatus` objects
- **After**: All state changes go through `StatusStore` with atomic operations
- **Result**: No more "Set changed size during iteration" errors

## Migration Path

To migrate to the new architecture:

1. **Keep old `stargate_core.py`** for reference
2. **Use `stargate_core_refactored.py`** as the new implementation
3. **Update imports** in `proxy/dependencies.py`:
   ```python
   from proxy.stargate_core_refactored import StargateProxy
   ```
4. **Test both paths**:
   - Test with scheduler disabled (direct processing)
   - Test with scheduler enabled (queue processing)

## Benefits of New Architecture

1. **Clean Separation of Concerns**
   - Each module has a single, well-defined responsibility

2. **No Code Duplication**
   - Request preparation happens once, used by both paths

3. **Truly Asynchronous**
   - Queue system handles model loading in background
   - No blocking in request handler

4. **Easy to Test**
   - Each component can be tested independently
   - Mock interfaces are clear

5. **Easy to Extend**
   - Add new transformations in `RequestPreparer`
   - Add new execution logic in `RequestExecutor`
   - Modify queue behavior in `SimpleQueue`

## File Structure

```
proxy/
├── stargate_core.py              # Old implementation (for reference)
├── stargate_core_refactored.py   # New implementation
├── core/
│   ├── request_preparer.py       # Request preparation
│   ├── request_executor.py       # Request execution
├── token_management/
│   └── token_manager.py          # Token counting
└── ...

src/scheduling/
├── gateway_monitor.py            # Gateway health/status monitoring
├── gateway_state_manager.py      # Gateway state management
├── simple_queue/                 # SimpleQueue implementation
│   ├── queue.py                  # Main queue class (FIFO)
│   ├── processing.py             # Queue processor
│   └── types.py                  # QueuedRequest dataclass
└── events.py                     # Event signal definitions
```

## Queue Processing

### Simple Queue (FIFO)

The `SimpleQueue` uses `asyncio.Queue` for lock-free request queuing with FIFO ordering.

**Architecture:**
- **Data Structure**: `asyncio.Queue` (built-in async coordination)
- **Ordering**: First-In-First-Out (FIFO) - requests processed in arrival order
- **Concurrency**: Managed by gateway-level `try_reserve_slot()` - no per-model semaphores
- **Capacity**: Fixed maximum size, immediate failure when full (no blocking)

**Key Components:**

1. **SimpleQueue** (`src/scheduling/simple_queue/queue.py`)
   - Manages request queue using `asyncio.Queue`
   - Provides enqueue/start/stop lifecycle
   - Tracks statistics (queued, processed, failed, timeout)
   - **Lock-free**: asyncio.Queue provides coordination internally

2. **QueueProcessor** (`src/scheduling/simple_queue/processing.py`)
   - Processes requests from queue concurrently
   - Concurrency managed by executor via `try_reserve_slot()` (gateway-level)
   - Uses `asyncio.wait_for(queue.get())` for non-blocking polling
   - **Lock-free**: Dictionary access is atomic in single-threaded async

3. **QueuedRequest** (`src/scheduling/simple_queue/types.py`)
   - Contains request context and async future for result
   - Tracks queuing timestamp for age calculation
   - **No priority field**: FIFO ordering only

**Request Flow:**

1. **Enqueue**: `SimpleQueue.enqueue_request(context) -> QueuedRequest`
   - Check capacity (raise RuntimeError if full)
   - Create QueuedRequest with future
   - Add to queue via `queue.put_nowait()`
   - Publish REQUEST_QUEUED event
   - Return QueuedRequest to caller

2. **Process**: `QueueProcessor.process_queue()`
   - Poll queue with timeout: `await asyncio.wait_for(queue.get(), timeout=0.05)`
   - Spawn concurrent task for each request
   - Executor handles capacity via `try_reserve_slot()` (no semaphore needed)
   - Call request processor (forwards to gateway)
   - Set future result/exception
   - Publish REQUEST_COMPLETED/REQUEST_FAILED events

3. **Result**: Caller awaits `QueuedRequest.future`
   - Future resolves when request completes
   - Returns response or raises exception
   - Timeout handling via `asyncio.wait_for(future, timeout)`

**Concurrency Control:**

- **Gateway-Level Capacity**: Managed by `try_reserve_slot()` in executor
- **No Per-Model Semaphores**: Removed in favor of gateway-level tracking
- **Cross-Model Concurrency**: Different models process concurrently
- **Task Management**: Active tasks tracked in set, graceful shutdown waits for completion

**Queue-Full Behavior:**

- Immediate failure (no blocking): Raises `RuntimeError` when queue is full
- Client receives 503 Service Unavailable
- Prevents request pileup during overload
- Makes capacity issues easier to diagnose

**Lock-Free Design:**

The queue implementation is lock-free because:
1. `asyncio.Queue` provides built-in coordination without explicit locks
2. Dictionary operations without `await` are atomic in single-threaded async
3. Only one coroutine runs at a time (preemption only at `await` points)
4. Gateway-level capacity tracking via `try_reserve_slot()` (atomic check-and-set)

**Why No Locks Are Needed:**

- **asyncio.Queue**: Internally handles coordination, no external lock needed
- **Gateway Tracking**: `try_reserve_slot()` provides atomic capacity management
- **Single-Threaded**: Python's async is cooperative multitasking, not preemptive
- **Queue Methods**: `put_nowait()`, `get()`, `qsize()`, `empty()` are thread-safe

**Removed Features:**

- **Priority Queue**: Removed in favor of simple FIFO ordering
- **Heapq**: Replaced with asyncio.Queue
- **Manual Locking**: Removed `_queue_lock` and `_semaphore_lock`
- **Currently Processing Tracking**: Removed in favor of task set tracking

## Configuration

The new architecture uses the same configuration file (`config/stargate_config.yaml`):

```yaml
request_queue:
  max_size: 1000             # Queue capacity - tune for backpressure control
  max_concurrent_processing: 10  # Maximum concurrent request processing
  request_timeout: 300       # Timeout in seconds for queued requests
  # Note: per_model_concurrency removed - capacity managed by gateway-level try_reserve_slot()
```

## Next Steps

1. Test the refactored implementation
2. Benchmark performance (queue vs direct)
3. Add integration tests
4. Update documentation
5. Deprecate old `stargate_core.py`

