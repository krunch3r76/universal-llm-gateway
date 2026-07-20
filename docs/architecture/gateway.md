# Universal LLM Gateway — Architecture Overview

<!-- GENERATED:START -->

> **Provenance disclaimer.** Everything below is derived exclusively from the
> module/class **docstrings and signatures** supplied in the inline
> declaration inventory for this request (retry of CDP `73436697`, which was
> refused for lacking filesystem access). No source file was opened, no
> import graph was traced, and no code was executed. Statements here describe
> what the code **declares itself to be** — not what it actually does at
> runtime. Where the inventory was silent, the corresponding section is
> marked `missing_coverage` rather than filled in from inference or prior
> knowledge of similar systems. Nothing in this document should be treated as
> a verified behavioral claim.

## 1. Workers & Controller

**`core/workers/controller/controller.py`**

Declares a `WorkerController` responsible for delegating model loading and
inference to lower-level collaborators. Per its docstring:

- Operates on **canonical model IDs only** — it does not accept the `:N`
  instance-suffixed form used elsewhere in the system.
- Enforces a **one-worker-per-model_id** relationship.
- Delegates to loader/unloader components and to chat-completion handlers
  (the docstring names these as collaborators but the inventory does not
  include their own declarations).

Declared methods: `__init__`, `_init_resource_monitoring`, `_init_paths`,
`_init_managers`, `_create_health_config`, `_cleanup_socket_file`,
`_create_transport_config`. The names suggest the constructor wires up
resource monitoring, filesystem paths, sub-managers, a health-check config,
and a transport config, and that a Unix socket file is cleaned up somewhere
in the controller's lifecycle — but the inventory gives no docstring detail
for any individual method, so none of that is asserted as confirmed
behavior.

**`core/workers/controller/__init__.py`**

A package-shadow module whose sole declared purpose is re-exporting
`WorkerController` from `controller.py`.

## 2. Model Registry

**`core/model_registry/registry/core.py`**

Declares `ModelRegistry`, described as composing catalog, loader, and
availability mixins into a single class for "managing model metadata and
validation." Declared methods: `__init__`, `__repr__`. The mixin
composition (catalog / loader / availability) is asserted by the module
docstring itself; the inventory does not include declarations for those
mixins individually, so their contents are `missing_coverage`.

**`core/model_registry/registry/__init__.py`**

Package-shadow module re-exporting `ModelRegistry` and `normalize_model_id`.
The existence of a `normalize_model_id` helper is inferred only from this
re-export declaration — its own docstring/signature was not part of the
inventory.

## 3. Events & Types

**`core/events/types/__init__.py`**

Declared as the package-shadow replacement for a former monolithic
`types.py`. Its stated purpose is re-exporting every event-signal constant
and every `@event_factory`-decorated helper, so that both
`from src.core.events.types import X` and the relative `from .types import X`
import forms continue to work unchanged after the package split.

**`core/events/types/model_lifecycle.py`**

Declares `MODEL_*` signal constants and `@event_factory` helpers for model
load/unload events. Per the docstring, these are consumed by `load_flow`,
the loader, the unloader, and the resource-tracker's crash-handling paths.
The docstring explicitly flags the signal **string values** as an event-bus
contract that must not be renamed without a corresponding update to
"event-contracts" (an artifact not included in this inventory).

## 4. VRAM Reconciler

**`core/resources/vram_reconciler/reconciler.py`**

Declares `VramReconciler`, composed (per its docstring) from "periodic VRAM
reconciliation concern mixins." Declared method: `__init__`. Stated
behavior contract:

- Runs on an interval named `RECONCILE_INTERVAL_S` (declared as a constant
  reference in the docstring; its value is not given in this inventory).
- Performs three sweeps:
  1. **Phantom sweep** — a running process not present in the tracked set is
     force-unloaded as an orphan.
  2. **Ghost sweep** — description was truncated in the source inventory
     ("tracke...") and cannot be reproduced verbatim; treat the ghost-sweep
     semantics as `missing_coverage` rather than guessed.
  3. A third, unnamed sweep referenced by "three sweeps" in the docstring
     but not enumerated in the inventory text — `missing_coverage`.

## 5. Config Manager

**`core/config_manager/__init__.py`**

Package-shadow module for `model_loaders.yaml` configuration handling,
declared to provide "validation and atomic writes." It re-exports
`ConfigManager`, unspecified "merge helpers," and error/result types,
explicitly including `deep_merge_dict` and `ConfigValidationError`, so that
existing imports of the form
`from src.core.config_manager import ConfigManager, deep_merge_dict, ConfigValidationError`
keep working. The underlying `config_manager.py` being shadowed is named but
not itself present in the inventory.

## 6. Load Flow

**`core/workers/model_operations/load_flow/__init__.py`**

Declared as the package covering "model loading flow operations: worker
lifecycle, verification, and finalization." The inventory contains only the
package-level docstring — no submodule, class, or function declarations for
the loading flow itself were supplied. Treat the internal structure of
load_flow as `missing_coverage`.

## 7. Routers

**`routers/api/v1/models/management/__init__.py`**

Declared as the "Model Configuration Management API Router," providing HTTP
endpoints for programmatic model-catalog management. Per its docstring, it
is secured by gateway configuration and optional token authentication. It is
itself a package-shadow of `management.py`, re-exporting `router` so that
`from src.routers.api.v1.models import management` and `management.router`
continue to resolve through `app_factory`.

No other router modules (e.g. catalog routers, audio params, websocket
routers referenced in the SLOC scan) had declarations included in this
inventory — their contents are `missing_coverage`.

## 8. Jobs

The scan summary lists several `jobs/measurement/*.py` files (`gpu.py`,
`execution.py`, `common.py`) by SLOC count only. **No docstrings, class
declarations, or function signatures for any jobs module were included in
the declaration inventory**, so this section cannot be populated. The entire
Jobs subsystem is `missing_coverage`.

## 9. Application Bootstrap (supplementary — present in inventory)

**`app/app_factory.py`** — Declared as the application factory for
creating FastAPI application instances. No further detail (functions,
classes) was included.

**`main.py`** — Declared as the main FastAPI entry point for the Universal
LLM Gateway inference service. Per its docstring, it bootstraps logging,
"engine environment," and socket configuration before constructing the app
via `app_factory`. It is stated to be invoked by uvicorn in factory mode, or
directly as `__main__`, and is the intended start point for gateway
operators and process supervisors.

<!-- GENERATED:END -->

---

## Coverage Notes

### unsupported_claims

None. No statement above goes beyond what the supplied docstrings/signatures
assert. Where a docstring implied but did not itself specify something (e.g.
the value of `RECONCILE_INTERVAL_S`, the full contents of the "ghost sweep,"
the mixins backing `ModelRegistry`), it is called out explicitly as
`missing_coverage` rather than filled in.

### missing_coverage

- `core/resources/vram_reconciler/reconciler.py` — ghost-sweep description
  was truncated in the source inventory; third sweep type unnamed.
- `core/workers/model_operations/load_flow/__init__.py` — no submodule/class/
  function declarations supplied for worker lifecycle, verification, or
  finalization steps.
- `core/workers/controller/controller.py` — method-level docstrings not
  supplied (only method names); resource monitoring, path, and manager
  initialization behavior not described beyond method naming.
- `core/model_registry/registry/core.py` — catalog/loader/availability mixin
  modules referenced but not themselves included in the inventory.
- Entire **Jobs** subsystem (`jobs/measurement/gpu.py`, `execution.py`,
  `common.py`) — SLOC-only, no declarations.
- Router surface beyond `routers/api/v1/models/management/__init__.py` —
  e.g. catalog routers (`routers/api/v1/catalog/get/mutations.py`), audio
  params (`routers/v1/audio/params.py`), websocket router state
  (`routers/ws/state.py`) appear only in the SLOC scan, not the declaration
  inventory.
- Supporting subsystems named in the SLOC scan but absent from the
  declaration inventory entirely: caching (`cache_manager.py`), resource
  monitoring events (`resource_monitoring.py`), inference streaming
  (`workers/inference/streaming.py`), synthetic models
  (`synthetic_models.py`), websocket messages/init cache, tool discovery
  (`tools/discovery.py`), resource tracker (`resources/tracker.py`), event
  persistence/aggregation, gateway_config, catalog schema, worker state
  machine, chat_completion streaming/non_streaming, batching.
- `core/model_registry/registry/__init__.py` — `normalize_model_id` is named
  only as a re-export target; its own signature/docstring was not supplied.

### human_markers

None found. The inventory as supplied contains no TODO/FIXME/XXX markers,
open questions, or explicit "needs human review" annotations — this reflects
the absence of such markers in the inventory text provided, not a
verification that the underlying source files lack them.

### loaded_skills

None. This is a plain-markdown drafting task from inline data with no
external lookups, code execution, or document-format conversion — none of
the available output-format skills (docx/pptx/xlsx/pdf) applied, and no
skill was invoked.
