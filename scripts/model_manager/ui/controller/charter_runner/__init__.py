"""Charter runner — CHECKPOINT continuation for standing roots.

Manage-hosted tick that admits one window per eligible enrolled root.
See ``cortex://notes/system/specs/charter-runner-tick.md``.

Per-root todo ``attendance`` is SOT for admission mode:

- **attended** (default) — unattended generate via ``seat=cursor-sdk``; hard stall
  env-opt-in only (``CHARTER_UNATTENDED_STALE_S``).
- **autonomous** — background-lead packet; hard stall **default-on** at
  ``DEFAULT_AUTONOMOUS_STALE_S`` (3600s, margin over CURSOR_SDK_TIMEOUT).
  Explicit ``CHARTER_UNATTENDED_STALE_S`` always wins (``0`` = force OFF).
  Incomplete windows self-heal after ``CHECKPOINT_MISSING_GRACE_S`` (``self_heal``).
  Consult-mode hung WIP past ``DEFAULT_CONSULT_STALE_S`` (900s) recovers via
  ``consult_stall`` (a:26131).
"""

from .reload import charter_runner_loop_class, reload_charter_runner_modules
from .tick_loop import CharterRunnerTickLoop

__all__ = [
    "CharterRunnerTickLoop",
    "charter_runner_loop_class",
    "reload_charter_runner_modules",
]
