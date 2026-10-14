"""External guarded manage quit/start — outside the manage PID.

Callers use this package (or ``python -m scripts.model_manager.guarded_manage_reexec``)
to refuse-then-reexec the manage host when charter_reload is insufficient. The
mechanism lives outside manage so a sealed Sunday process cannot soft-veto its
own replacement. Entry points are the CLI and ``--detach``. Manage stays out of
``VALID_SERVICES`` / in-process ``sync_restart``.
"""

from .result import GuardedReexecResult

__all__ = ["GuardedReexecResult", "run_guarded_reexec"]


def __getattr__(name: str):
    if name == "run_guarded_reexec":
        from .runner import run_guarded_reexec

        return run_guarded_reexec
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
