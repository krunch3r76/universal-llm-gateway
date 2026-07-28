"""Charter runner — CHECKPOINT continuation for standing roots.

Manage-hosted tick that admits one window per eligible enrolled root.
See ``cortex://notes/system/specs/charter-runner-tick.md``.

Phase 3: kernel is sole admitter; heal/reload/eligibility/parse/materializer
surfaces deleted or absorbed into kernel packages.
"""

from .kernel import CharterRunnerTickLoop

__all__ = [
    "CharterRunnerTickLoop",
]
