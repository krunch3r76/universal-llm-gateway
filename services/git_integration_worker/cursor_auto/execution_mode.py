"""Structural execution-mode predicate for concurrent admission opt-in.

Mission 9440 ships this mechanism default-deny and empty. Populating
``_CONCURRENT_EXECUTION_MODES`` for a real class is `todo:auto-admit-
concurrency`'s job -- each entry must first answer the 9031-turn-80
lease-context question (does running N instances of this class concurrently
risk two processes holding the same write-lease / mutating the same
checkout?) before it is added here. This predicate is intentionally the
ONLY place that decision is expressed -- callers must branch on
``is_concurrent_execution_mode(job.execution_mode)``, never re-derive an
equivalent check from ``job.contract`` or any other field.
"""

from __future__ import annotations

DEFAULT_EXECUTION_MODE = "serial"

# Default-deny. Empty in production -- see module docstring. Do not add an
# entry here without a cited lease-context answer for that class.
_CONCURRENT_EXECUTION_MODES: frozenset[str] = frozenset()


def is_concurrent_execution_mode(execution_mode: str | None) -> bool:
    """True only for a class that has been explicitly opted in.

    Structural predicate: the input is the declared ``execution_mode``
    string, never ``contract``, never inferred from any other job field.
    """
    return bool(execution_mode) and execution_mode in _CONCURRENT_EXECUTION_MODES
