"""External guarded manage quit/start — outside the manage PID.

Callers use this package (or ``python -m scripts.model_manager.guarded_manage_reexec``)
to refuse-then-reexec the manage host when charter_reload is insufficient. The
mechanism lives outside manage so a sealed Sunday process cannot soft-veto its
own replacement. Propagate admits ``service=manage`` and
``handler_propagation`` calls ``run_guarded_reexec`` (external to the manage
PID). Manage stays out of ``VALID_SERVICES`` / in-process ``sync_restart``.
"""

from .result import GuardedReexecResult
from .runner import run_guarded_reexec

__all__ = ["GuardedReexecResult", "run_guarded_reexec"]
