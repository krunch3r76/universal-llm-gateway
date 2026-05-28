"""
Engine-mediated iterative loop handler package (assess_loop_v1).

Package-shadow of the former ``assess_loop.py`` module. Re-exports
``AssessLoopHandler`` as the sole public surface so the side-effect import
``from .. import assess_loop as _assess_loop`` (handlers/builtin/__init__.py:20)
continues to trigger ``@register_handler`` registration of
``AssessLoopHandler`` with the DomainRouter when this package is imported.

The handler calls a model to assess the current state, interprets its
structured JSON decision, dispatches the chosen action (a model call with a
different prompt), accumulates the result, and repeats until the model signals
completion or the budget is exhausted.

Critical lifecycle invariant (enforced in ``loop_runner.run_assess_loop``):
- ∀ run_assess_loop(): AssessLoopStarted ⟹ ∃! AssessLoopCompleted (finally block)
- ∀ action call: returns plain text → becomes new artifact for next iteration
- ∀ assess call: returns JSON matching assess_schema
- artifact_key identifies which handler_input is the mutable artifact
- ∀ programmatic handler returning "artifact" key: value replaces loop artifact
  (popped before storing in last_decision to avoid bloating history)

Internal layout (all submodules are package-private):

- ``handler`` — AssessLoopHandler class (thin delegator + validate)
- ``loop_runner`` — run_assess_loop orchestrator; owns the try/finally invariant
- ``setup`` — handler_inputs resolution, base_ctx, cached system prompt
- ``initial_action`` — optional pre-loop initial action
- ``assess_phase`` — one-iteration assess (programmatic or LLM + JSON parse)
- ``action_phase`` — multi-step action dispatch
- ``artifact_text`` — pure artifact/text-shaping helpers
"""

from .handler import AssessLoopHandler

__all__ = ["AssessLoopHandler"]
