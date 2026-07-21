"""Charter runner — CHECKPOINT continuation for standing roots.

Manage-hosted tick that admits one window per eligible enrolled root.
See ``cortex://notes/system/specs/charter-runner-tick.md``.

Two substrates (both use ``packet_path``, never ``source_ref``), selected via
``CHARTER_ADMISSION_MODE`` (default ``generate``):

- **Attended** — ``CHARTER_ADMISSION_MODE=handoff``: POST
  ``/api/v1/team/handoff`` with ``role=cursor-consult``; Composer on an IDE
  bus thread (``from=cursor``, human opens the thread). Soft ``waiting_open``
  remind only; no default hard-fail.
- **Unattended** — default: manage fires ``seat=cursor-sdk``, ``op=generate``,
  ``contract=light-bounded``, ``model=cursor/grok-4.5`` via team dispatch.
  Optional hard stall via ``CHARTER_UNATTENDED_STALE_S`` (default OFF).

Do not label the code contract as bare ``cursor-consult`` — that handoff role
is an attended operator path, not the unattended runner wire.
"""

from .reload import charter_runner_loop_class, reload_charter_runner_modules
from .tick_loop import CharterRunnerTickLoop

__all__ = [
    "CharterRunnerTickLoop",
    "charter_runner_loop_class",
    "reload_charter_runner_modules",
]
