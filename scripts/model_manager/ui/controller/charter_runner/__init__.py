"""Charter runner — CHECKPOINT continuation for standing roots.

Manage-hosted tick that admits one window per eligible enrolled root.
See ``cortex://notes/system/specs/charter-runner-tick.md``.

Phase 3: kernel is sole admitter; heal/reload surfaces deleted.
"""

from .tick_loop import CharterRunnerTickLoop

__all__ = [
    "CharterRunnerTickLoop",
]
