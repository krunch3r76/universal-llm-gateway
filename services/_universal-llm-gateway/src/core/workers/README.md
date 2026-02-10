# Workers Module

## Overview

The workers module provides process-isolated worker management for LLM inference, including model loading, RPC communication, health monitoring, and event-driven cleanup.

**Primary Pattern**: Process Isolation
- Workers run in separate processes managed by ProcessSupervisor
- Communication via RPC over Unix domain sockets (Universal Protocol)
- Event-driven cleanup and lifecycle management
- State machine-based worker lifecycle

## Directory Structure

```
src/core/workers/
├── __init__.py                          # Public API exports
├── controller.py                        # WorkerController (high-level API)
├── manager.py                           # Legacy manager (transitioning)
├── exceptions.py                        # Worker-specific exceptions
├── worker_types.py                      # Worker types and enums
├── utils.py                             # Worker utilities
├── process/                             # Process isolation (PRIMARY PATTERN)
│   ├── __init__.py
│   ├── lifecycle.py                     # ProcessLifecycleManager
│   ├── state.py                         # Shared process state
│   ├── crash_callback.py                # Crash detection
│   ├── kill.py                          # Process termination
│   └── communication/                   # RPC communication (event-driven)
│       ├── __init__.py                  # Re-exports + event registration
│       ├── manager.py                   # ProcessCommunicationManager (238 SLOC)
│       ├── orchestration.py             # Loading flow coordination (281 SLOC)
│       ├── config_builder.py            # Model config construction (83 SLOC)
│       ├── rpc_client.py                # RPC socket communication (98 SLOC)
│       ├── health_checks.py             # Health validation (93 SLOC)
│       ├── error_handling.py            # Error handling utilities (143 SLOC)
│       ├── cleanup.py                   # Event-driven cleanup (188 SLOC)
│       └── event_handlers.py            # Cleanup event handlers (89 SLOC)
├── worker/                              # Worker process implementation
│   ├── __main__.py                      # Worker entrypoint
│   ├── process.py                       # Worker process logic
│   ├── engine_lifecycle.py              # Engine lifecycle management
│   ├── rpc/                             # RPC request handlers
│   │   ├── inference.py                 # Inference handlers
│   │   ├── lifecycle.py                 # Lifecycle handlers
│   │   ├── load.py                      # Model loading handlers
│   │   ├── metadata.py                  # Metadata handlers
│   │   └── streams.py                   # Stream handlers
│   └── stream/                          # Stream processing
│       ├── inference_start.py           # Start streaming inference
│       ├── inference_run.py             # Run streaming inference
│       └── audio_rpc.py                 # Audio streaming RPC
├── model_operations/                    # Model loading/unloading
│   ├── load_flow.py                     # Model loading flow
│   ├── loader.py                        # Model loader
│   ├── preflight.py                     # Preflight checks
│   └── unloader.py                      # Model unloader
├── inference/                           # Inference management
│   ├── regular.py                       # Regular inference
│   ├── streaming.py                     # Streaming inference
│   └── cancellation.py                  # Inference cancellation
├── chat_completion/                     # Chat completion handling
│   ├── streaming.py                     # Streaming chat completions
│   └── non_streaming.py                 # Non-streaming chat completions
├── cancellation/                        # Stream cancellation
│   └── emission.py                      # Cancellation event emission
├── monitoring/                          # Process monitoring
│   └── process_monitor.py               # Process health monitoring
├── process_crash_bridge.py              # Crash event bridge
├── socket_cleanup_handler.py            # Socket cleanup coordination
├── resource_tracker_crash_handler.py    # Resource tracker crash handling
├── stream_cancellation_handler.py       # Stream cancellation handling
├── orphan_detector.py                   # Orphaned process detection
└── state_machine.py                     # Worker state machine
```

## Core Components

### WorkerController

High-level API for worker management. Coordinates model loading, inference, and lifecycle operations.

```python
from src.core.workers import WorkerController

controller = WorkerController(...)
await controller.load_model("model-id")
response = await controller.chat_completion("model-id", messages)
```

### ProcessLifecycleManager

Manages worker process lifecycle: startup, health monitoring, crash recovery, and shutdown.

Located in `process/lifecycle.py`, this component handles:
- Process supervision and monitoring
- Crash detection and recovery
- Process termination and cleanup
- Health check coordination

### ProcessCommunicationManager

Handles RPC communication with workers. Orchestrates model loading flow:

**Flow**: config building → RPC calls → health validation → cleanup (event-driven)

Located in `process/communication/manager.py`, this component coordinates:
- Model configuration construction
- RPC socket communication
- Health validation
- Event-driven cleanup

### Event-Driven Cleanup

Cleanup operations publish events instead of using callbacks:

| Event | Purpose | Published From |
|-------|---------|----------------|
| `WorkerCleanupRequested` | Overall cleanup orchestration | `cleanup.py` |
| `ResourceStateUpdateRequested` | Resource tracker updates | `cleanup.py` |
| `SocketCleanupRequested` | Socket file removal | `cleanup.py` |
| `SupervisorTerminationRequested` | Supervisor termination | `cleanup.py` |

**Invariant**: ∀ module ∈ communication/: SLOC(module) ≤ 300

Event handlers are registered during app startup via `register_cleanup_event_handlers()`.

## Worker Lifecycle

```
UNINITIALIZED → LOADING → LOADED ↔ BUSY → UNLOADING → UNLOADED
                   ↓         ↓       ↓
                 ERROR ←────────────┘
```

**Key Points**:
- Process isolation via ProcessSupervisor
- RPC communication over Unix domain sockets
- Event-driven state transitions
- Automatic crash recovery via crash callbacks

## Communication Module

The `process/communication/` module handles all worker communication via RPC:

| Module | Responsibility | SLOC |
|--------|----------------|------|
| `manager.py` | High-level orchestration | 238 |
| `orchestration.py` | Loading flow coordination | 281 |
| `config_builder.py` | Model config construction | 83 |
| `rpc_client.py` | RPC socket communication | 98 |
| `health_checks.py` | Health validation | 93 |
| `error_handling.py` | Error handling utilities | 143 |
| `cleanup.py` | Event-driven cleanup (publishes events) | 188 |
| `event_handlers.py` | Cleanup event handlers (subscribes to events) | 89 |

**Total**: 8 modules, 1,213 SLOC (split from single 947-line file)

## Usage

### Basic Usage (High-Level API)

```python
from src.core.workers import WorkerController

controller = WorkerController(...)
await controller.load_model("model-id")
response = await controller.chat_completion("model-id", messages)
```

### Advanced Usage (Process-Level API)

```python
from src.core.workers.process import ProcessLifecycleManager
from src.core.workers.process.communication import ProcessCommunicationManager

lifecycle = ProcessLifecycleManager(...)
comm = ProcessCommunicationManager(...)
```

### Event Registration

Cleanup event handlers must be registered during app startup:

```python
from src.core.workers.process.communication import register_cleanup_event_handlers

# In app startup (see src/app/lifecycle.py)
register_cleanup_event_handlers()
```

This registers handlers for:
- `WorkerCleanupRequested` → `handle_worker_cleanup()`
- `ResourceStateUpdateRequested` → `handle_resource_state_update()`
- `SocketCleanupRequested` → `handle_socket_cleanup()`
- `SupervisorTerminationRequested` → `handle_supervisor_termination()`

## Module Organization

| Module | Purpose |
|--------|---------|
| `controller.py` | High-level worker coordination |
| `process/lifecycle.py` | Process lifecycle management |
| `process/communication/` | RPC communication (8 modules, event-driven) |
| `model_operations/` | Model loading/unloading flows |
| `inference/` | Inference management (regular, streaming, cancellation) |
| `chat_completion/` | Chat completion handling |
| `worker/` | Worker process implementation |
| `worker/rpc/` | RPC request handlers (inference, lifecycle, load, metadata, streams) |
| `cancellation/` | Stream cancellation coordination |
| `monitoring/` | Process health monitoring |

## Migration Notes

### Removed (Old Structure)

- `simple_process_manager.py` → Replaced by `process/lifecycle.py`
- Flat `worker.py` → Replaced by `worker/` directory with RPC handlers
- Single `communication.py` (947 lines) → Split into `communication/` (8 modules)
- Callback-based cleanup → Event-driven cleanup (4 events)
- Old scattered imports → Consolidated module exports

### Added (Current Structure)

- `process/` directory (process isolation pattern)
- `communication/` subdirectory (8 modules, event-driven)
- RPC-based communication (Universal Protocol)
- Event-driven cleanup (4 events)
- Structured `worker/` directory with RPC handlers
- `model_operations/` directory for loading flows
- `monitoring/` directory for health monitoring

### Import Migration

```python
# ❌ Old (no longer available)
from src.core.simple_process_manager import SimpleProcessManager
from src.core.worker_controller import WorkerController

# ✅ New (current)
from src.core.workers import WorkerController
from src.core.workers.process import ProcessLifecycleManager
from src.core.workers.process.communication import ProcessCommunicationManager
```

## Benefits

1. **Better Organization**: Process isolation pattern with clear module boundaries
2. **Event-Driven Architecture**: Cleanup via events instead of callbacks
3. **Modular Communication**: 8 focused modules instead of single 947-line file
4. **SLOC Compliance**: All modules ≤300 SLOC (adhering to modularization rules)
5. **Improved Testability**: Clear separation of concerns enables focused unit tests
6. **RPC-Based**: Universal Protocol for worker communication
7. **Type Safety**: Comprehensive type hints throughout

## References

- **Event Details**: See `services/_universal-llm-gateway/README_AI.md` for event emission/consumption
- **Architecture**: See `.cursor/rules/architecture.mdc` for process isolation pattern
- **Modularization**: See `.cursor/rules/modularization.mdc` for SLOC limits and structure rules
