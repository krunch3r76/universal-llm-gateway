"""Charter runner — CHECKPOINT continuation for standing roots.

Manage-hosted tick that admits one window per eligible enrolled root.
See ``cortex://notes/system/specs/charter-runner-tick.md``.

Admission modes (``CHARTER_ADMISSION_MODE`` / durable admission_mode file):

- **Attended** — ``handoff``: POST ``/api/v1/team/handoff`` with
  ``role=cursor-consult``; soft ``waiting_open`` remind only unless
  ``CHARTER_UNATTENDED_STALE_S`` is explicitly set.
- **Unattended generate** — default: ``seat=cursor-sdk`` generate; hard stall
  env-opt-in only.
- **Autonomous** — background-lead packet; hard stall **default-on** at
  ``DEFAULT_AUTONOMOUS_STALE_S`` (3600s, margin over CURSOR_SDK_TIMEOUT).
  Explicit ``CHARTER_UNATTENDED_STALE_S`` always wins (``0`` = force OFF;
  malformed → treated as unset → default; negative → force OFF).
  Incomplete windows (worker ``complete``/``partial`` without a bound root
  terminal after the WIP pointer) self-heal after ``CHECKPOINT_MISSING_GRACE_S``
  from worker closeout via a machine CHECKPOINT that re-queues Next-pickup
  (see ``self_heal``; R-admit A1–A7). Consult-mode hung WIP past
  ``DEFAULT_CONSULT_STALE_S`` (900s) recovers via ``consult_stall`` (R-ADMIT on
  root advances; else re-queue CONSULT_PENDING) — a:26131.

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
