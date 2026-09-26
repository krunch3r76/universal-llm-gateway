"""Backward-compatible import path re-exporting ``DAGExecutor`` from ``dag_executor``.

The real implementation lives in ``execution/dag_executor/executor``. This shim keeps
``from .executor import DAGExecutor`` working for older modules such as
``disconnect_monitor`` (TYPE_CHECKING import). It contains no logic; add nothing here.
"""

from .dag_executor.executor import DAGExecutor

__all__ = ["DAGExecutor"]
