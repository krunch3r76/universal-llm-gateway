"""Charter runner — CHECKPOINT continuation for standing enrollments.

Manage-hosted supervisor that launches one run per eligible enrollment on the roster.
See ``cortex://notes/system/specs/charter-runner-tick.md``.

Phase 3: kernel is sole launcher; heal/reload/eligibility/parse/materializer
surfaces deleted or absorbed into kernel packages.
"""

from .kernel import CharterRunnerTickLoop

__all__ = [
    "CharterRunnerTickLoop",
]
