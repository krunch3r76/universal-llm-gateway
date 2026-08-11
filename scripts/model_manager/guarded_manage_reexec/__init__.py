"""External guarded manage quit/start — outside the manage PID.

Callers use this package (or ``python -m scripts.model_manager.guarded_manage_reexec``)
to refuse-then-reexec the manage host when charter_reload is insufficient. The
mechanism lives outside manage so a sealed Sunday process cannot soft-veto its
own replacement. Not wired into ``propagate`` or ``VALID_SERVICES``.
"""

from .result import RECOVERY_PATH, GuardedReexecResult
from .runner import run_guarded_reexec

__all__ = ["GuardedReexecResult", "RECOVERY_PATH", "run_guarded_reexec"]
