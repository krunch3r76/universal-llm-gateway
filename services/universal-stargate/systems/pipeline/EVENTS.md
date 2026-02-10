# Pipeline System Events

Pipeline system events track the complete execution lifecycle from pipeline start to completion, including step execution, checkpoint operations, and map step iteration progress.

**Source**: `core/events.py`

## Categories

| Category | Purpose | Failure Handling |
|----------|---------|------------------|
| **Pipeline Lifecycle** | Track overall pipeline execution from start to completion | Fatal: `pipeline.failed` terminates execution |
| **Step Lifecycle** | Track individual step execution progress | Fatal: `step.failed` terminates pipeline |
| **Map Step** | Fine-grained observability into parallel map execution | Per iteration tracking |
| **Checkpoint** | Track save/load lifecycle of step checkpoints | Non-fatal: execution continues |

---

## Pipeline Lifecycle Events

### `pipeline.started`

**Factory**: `PipelineStarted()`

Emitted when pipeline execution begins, after context creation and before DAG execution.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `domain` | `str` | Pipeline domain |
| `step_count` | `int` | Total number of steps in DAG |
| `timeout_seconds` | `float \| None` | Optional timeout from runtime options |

**Emitter**: `PipelineExecutor.execute()`

---

### `pipeline.completed`

**Factory**: `PipelineCompleted()`

Emitted when pipeline execution completes successfully, after DAG execution finishes.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `duration_seconds` | `float` | Total execution time |
| `step_count` | `int` | Number of steps executed |
| `output_step` | `str` | Final output step reference |

**Emitter**: `PipelineExecutor.execute()`

---

### `pipeline.failed`

**Factory**: `PipelineFailed()`

Emitted when pipeline execution fails, before exception propagates.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `duration_seconds` | `float` | Time until failure |
| `error` | `str` | Error message |
| `failed_step` | `str \| None` | Name of step that failed (if detected) |

**Emitter**: `PipelineExecutor.execute()`

---

### `pipeline.cancelled`

**Factory**: `PipelineCancelled()`

Emitted when pipeline execution is cancelled due to client disconnection.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `duration_seconds` | `float` | Time until cancellation |
| `reason` | `str` | Cancellation reason (e.g., "client_disconnected") |
| `completed_steps` | `int` | Number of steps completed before cancellation |
| `pending_steps` | `int` | Number of steps that were pending/running |

**Emitter**: `PipelineExecutor.execute()`

---

## Step Lifecycle Events

### `pipeline.step.started`

**Factory**: `StepStarted()`

Emitted when step execution begins, before the step runs.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `step_name` | `str` | Step identifier |
| `step_type` | `str` | Step type (e.g., "generate", "map", "transform") |
| `model_id` | `str \| None` | Target model (if applicable) |
| `is_map_step` | `bool` | True if this is a map step |

**Emitter**: `DAGExecutor._execute_step()`

---

### `pipeline.step.completed`

**Factory**: `StepCompleted()`

Emitted when step execution completes successfully, after output is available.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `step_name` | `str` | Step identifier |
| `duration_seconds` | `float` | Step execution duration |
| `output_length` | `int \| None` | Length of output text (if available) |
| `prompt_tokens` | `int` | Total prompt tokens (auto-aggregated for multi-call steps) |
| `completion_tokens` | `int` | Total completion tokens (auto-aggregated) |
| `model_call_count` | `int` | Number of model calls for this step |

**Emitter**: `DAGExecutor._record_success()`

---

### `pipeline.step.failed`

**Factory**: `StepFailed()`

Emitted when step execution fails, after exception is caught.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `step_name` | `str` | Step identifier |
| `duration_seconds` | `float` | Time until failure |
| `error` | `str` | Error message |

**Emitter**: `DAGExecutor._record_failure()`

---

### `pipeline.step.skipped`

**Factory**: `StepSkipped()`

Emitted when step is skipped due to condition evaluation failure.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `step_name` | `str` | Step identifier |
| `reason` | `str` | Skip reason (e.g., "condition not met") |

**Emitter**: `DAGExecutor._filter_ready_steps()`

---

## Checkpoint Events

### `pipeline.checkpoint.saved`

**Factory**: `CheckpointSaved()`

Emitted after checkpoint successfully saved to storage.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `step_name` | `str` | Step checkpointed |
| `checkpoint_key` | `str` | Storage key |
| `storage_backend` | `str` | Backend (e.g., "filesystem") |

**Emitter**: `CheckpointManager._emit_saved()`

---

### `pipeline.checkpoint.loaded`

**Factory**: `CheckpointLoaded()`

Emitted when step resumed from checkpoint.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `step_name` | `str` | Step resumed |
| `checkpoint_key` | `str` | Storage key |
| `storage_backend` | `str` | Backend type |
| `saved_at` | `str` | ISO timestamp of original save |

**Emitter**: `CheckpointManager._emit_loaded()`

---

### `pipeline.checkpoint.failed`

**Factory**: `CheckpointFailed()`

Emitted when checkpoint operation fails (non-fatal; execution continues).

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `step_name` | `str` | Step where operation failed |
| `operation` | `str` | `"save"` or `"load"` |
| `error` | `str` | Error message |

**Emitter**: `CheckpointManager._emit_failed()`

---

## Map Step Events

### `pipeline.map.started`

**Factory**: `MapStepStarted()`

Emitted when map step begins execution.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `step_name` | `str` | Map step name |
| `total_iterations` | `int` | Iterations to execute |
| `timeout_seconds` | `float \| None` | Timeout (None if disabled) |
| `threshold` | `int \| float \| None` | Success threshold |

**Emitter**: `MapExecutor.execute()`

---

### `pipeline.map.iteration.started`

**Factory**: `MapIterationStarted()`

Emitted when single iteration dispatched.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `step_name` | `str` | Map step name |
| `iteration_index` | `int` | Zero-based index |
| `model_id` | `str \| None` | Target model |
| `gateway_id` | `str \| None` | Target gateway |

**Emitter**: `MapExecutor` (fail-fast/threshold paths)

---

### `pipeline.map.iteration.completed`

**Factory**: `MapIterationCompleted()`

Emitted when single iteration completes successfully.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `step_name` | `str` | Map step name |
| `iteration_index` | `int` | Zero-based index |
| `duration_seconds` | `float` | Actual iteration execution time (from start to completion) |

**Emitter**: `MapExecutor` (fail-fast/threshold paths)

---

### `pipeline.map.iteration.failed`

**Factory**: `MapIterationFailed()`

Emitted when single iteration fails.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `step_name` | `str` | Map step name |
| `iteration_index` | `int` | Zero-based index |
| `error` | `str` | Error message |
| `duration_seconds` | `float \| None` | Execution duration |
| `failure_type` | `str` | `"error"`, `"timeout"`, or `"cancelled"` |

**Emitter**: `MapExecutor` (fail-fast/threshold paths)

---

### `pipeline.map.timeout.warning`

**Factory**: `MapTimeoutWarning()`

Emitted at 75% and 90% of timeout for proactive alerting.

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `step_name` | `str` | Map step name |
| `elapsed_seconds` | `float` | Time elapsed |
| `timeout_seconds` | `float` | Configured timeout |
| `pending_iterations` | `list[int]` | Pending iteration indices |
| `completed_iterations` | `int` | Completed count |
| `percent_elapsed` | `float` | 75.0 or 90.0 |

**Emitter**: `MapExecutor._monitor_timeout()`

---

### `pipeline.map.completed`

**Factory**: `MapStepCompleted()`

Emitted when map step finishes (success or failure).

**Payload**:
| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | `str` | Pipeline identifier |
| `execution_id` | `str` | Execution UUID |
| `step_name` | `str` | Map step name |
| `succeeded_count` | `int` | Successful iterations |
| `failed_count` | `int` | Failed iterations |
| `total_count` | `int` | Total iterations |
| `duration_seconds` | `float` | Total duration |
| `met_threshold` | `bool` | Threshold met |

**Emitter**: `MapExecutor.execute()`

---

## Naming Conformance

**Status**: Checkpoint events violate [Event Naming Specification](../EVENTS.md) (PascalCase vs dot-notation).

**Migration required**:
| Current (⚠ non-conforming) | Target (conforming) |
|---------------------------|---------------------|
| `CheckpointSaved` | `pipeline.checkpoint.saved` |
| `CheckpointLoaded` | `pipeline.checkpoint.loaded` |
| `CheckpointFailed` | `pipeline.checkpoint.failed` |

---

## Event Flows

### Pipeline Execution
```
PipelineStarted
  ∀ step:
    StepStarted
      (if map step)
        MapStepStarted
          ∀ iteration:
            MapIterationStarted
            → MapIterationCompleted | MapIterationFailed
          (75%, 90% timeout) → MapTimeoutWarning
        MapStepCompleted
    → StepCompleted | StepFailed | StepSkipped
PipelineCompleted | PipelineFailed | PipelineCancelled
```

### Checkpoint
```
CheckpointManager.save() → CheckpointSaved
CheckpointManager.load() → CheckpointLoaded
```

### Map Step (Detailed)
```
MapStepStarted
  ∀ iteration:
    MapIterationStarted
    → MapIterationCompleted | MapIterationFailed
  (75%, 90% timeout) → MapTimeoutWarning
MapStepCompleted
```

## Event Timeline Example

For a successful pipeline with 3 steps (2 regular + 1 map):
```
pipeline.started                           (PipelineExecutor)
  pipeline.step.started                    (DAGExecutor, step 1)
  pipeline.step.completed
  pipeline.step.started                    (DAGExecutor, step 2 - map)
    pipeline.map.started                   (MapExecutor)
      pipeline.map.iteration.started       (iteration 0)
      pipeline.map.iteration.completed
      pipeline.map.iteration.started       (iteration 1)
      pipeline.map.iteration.completed
    pipeline.map.completed
  pipeline.step.completed
  pipeline.step.started                    (DAGExecutor, step 3)
  pipeline.step.completed
pipeline.completed                         (PipelineExecutor)
```

For a pipeline cancelled due to client disconnect:
```
pipeline.started                           (PipelineExecutor)
  pipeline.step.started                    (DAGExecutor, step 1)
  pipeline.step.completed
  pipeline.step.started                    (DAGExecutor, step 2)
    🔌 Client disconnects during step 2
pipeline.cancelled                         (PipelineExecutor, reason="client_disconnected")
```

## Correlation

All events include `pipeline_id` and `execution_id` for cross-lifecycle correlation.
