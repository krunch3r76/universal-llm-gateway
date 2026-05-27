"""Package re-exports for DAG executor step lifecycle observability.

Mirrors the prior monolith's public surface: a single ``StepObservability``
class consumers instantiate from ``executor.py`` and dispatch method calls
through. The split keeps free-function bodies in sibling submodules while
the class shell preserves the full method surface as thin delegators.

External consumers continue to use:

    from .observability import StepObservability

with no import-path change after the package-shadow split.

Submodule layout:

- ``step_observability`` — ``StepObservability`` class shell + delegators
- ``context``            — event-bus context resolution + publish
- ``step_lifecycle``     — per-step lifecycle emits (condition/skip/start/inputs)
- ``pipeline_boundaries`` — pipeline-boundary emits
  (timeout/deadlock/cancel/dag-completed)
- ``model_gate``         — model-gate emits (deferred/claimed/released/lookup-failed)
- ``outcomes``           — terminal-state recording (success / failure)
- ``input_capture``      — handler-binding resolution and snapshotting
- ``model_call_logging`` — per-step model-call execution-logger summary
"""

from .step_observability import StepObservability

__all__ = ["StepObservability"]
