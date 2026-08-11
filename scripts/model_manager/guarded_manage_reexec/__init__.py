"""External guarded manage quit/start — outside the manage PID.

Callers use this package (or ``python -m scripts.model_manager.guarded_manage_reexec``)
to refuse-then-reexec the manage host when charter_reload is insufficient. The
mechanism lives outside manage so a sealed Sunday process cannot soft-veto its
own replacement. Not wired into ``propagate`` or ``VALID_SERVICES``.
"""

from .runner import GuardedReexecResult, run_guarded_reexec

__all__ = ["GuardedReexecResult", "run_guarded_reexec"]
