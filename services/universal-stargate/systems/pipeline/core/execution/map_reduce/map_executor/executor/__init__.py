"""Package re-exports for the map step fan-out executor.

Mirrors the prior monolith's public surface: a single ``MapExecutor`` class
consumers instantiate from ``map_executor/__init__.py`` (and transitively from
``map_reduce/`` and ``execution/`` re-export chains, plus
``dag_executor/executor.py`` lazy import inside ``_execute_map_step``). The
split keeps free-function bodies in sibling submodules while the class shell
preserves the full method surface as thin delegators.

Internal layout (all submodules are package-private):

- ``protocols`` — MapIterationRuntimeProtocol, MapIterationHandlerProtocol
- ``map_executor`` — MapExecutor class shell + __init__ + thin delegators
- ``execute_flow`` — execute() orchestration body
- ``scheduled_iteration`` — extracted tracked / scheduled iteration helpers
- ``inference_boundary`` — primary + deferred inference-start signal handling
- ``iteration_execution`` — per-iteration execution + checkpoint + fingerprint
- ``iteration_context`` — per-iteration context + runtime decoration
- ``capacity`` — auto-derived max_concurrency from federated model capacity
"""

from .map_executor import MapExecutor

__all__ = ["MapExecutor"]
