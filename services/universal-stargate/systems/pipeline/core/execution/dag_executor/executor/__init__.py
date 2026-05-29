"""Package re-exports for the DAG step executor.

Mirrors the prior monolith's public surface: a single ``DAGExecutor`` class.
Consumers continue to import it unchanged via the parent
``dag_executor/__init__.py`` (``from .executor import DAGExecutor``) and the
transitive ``execution/`` → ``core/`` → ``pipeline/`` re-export chain, plus the
sibling ``model_coordination`` (TYPE_CHECKING) and ``observability`` modules
that reference ``from ..executor import DAGExecutor``. The package-shadow split
keeps free-function bodies in sibling submodules while the class shell preserves
the full method surface as thin delegators.

Internal layout (all submodules are package-private):

- ``dag_executor`` — ``DAGExecutor`` class shell: ``__init__`` + thin delegators
- ``lifecycle`` — proxy-client lifecycle + top-level ``execute`` loop
- ``scheduling`` — ready-step filtering, model-gated launch, dependency
  propagation, terminal-state counts
- ``completions`` — await pending tasks + fail-fast failure handling
- ``step_runner`` — per-step condition gate, wrapper-chain run, model fallback
- ``map_step`` — map-step delegation to ``MapExecutor``

DAGExecutor invariants (authoritative copy lives on the class docstring):
single-writer of ``context.outputs``; dependencies complete before a step
starts; first failure cancels remaining (fail-fast); ``SKIPPED`` satisfies a
dependency but skip propagation is not automatic.
"""

from .dag_executor import DAGExecutor

__all__ = ["DAGExecutor"]
