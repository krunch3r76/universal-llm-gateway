"""Charter kernel host + shadow (Phase 3 — tick_loop absorbed)."""

from __future__ import annotations

from .. import bus_client  # noqa: F401 — re-export for monkeypatch paths
from ..giw_live_hold import build_tick_env_snapshot, probe_giw_live_hold
from ..harvest import completed_windows, harvest_completed_windows
from .host import (
    DEFAULT_AUTONOMOUS_STALE_S,
    DEFAULT_CONSULT_STALE_S,
    CharterRunnerTickLoop,
    maybe_heal_admit_intent_orphan,
)
from .interval import (
    DEFAULT_TICK_INTERVAL_S,
    ENV_TICK_INTERVAL_S,
    tick_interval_from_env,
)
from . import hold
from .shadow import (
    SHADOW_LEDGER_STARVE_ROOT,
    SHADOW_STARVE_CLASS,
    ShadowDiffRow,
    ShadowKernel,
    ShadowPassResult,
    backfill_shadow_classifications,
    record_shadow_pass,
    run_shadow_for_roots,
)

__all__ = [
    "DEFAULT_AUTONOMOUS_STALE_S",
    "DEFAULT_CONSULT_STALE_S",
    "DEFAULT_TICK_INTERVAL_S",
    "ENV_TICK_INTERVAL_S",
    "CharterRunnerTickLoop",
    "SHADOW_LEDGER_STARVE_ROOT",
    "SHADOW_STARVE_CLASS",
    "ShadowDiffRow",
    "ShadowKernel",
    "ShadowPassResult",
    "backfill_shadow_classifications",
    "build_tick_env_snapshot",
    "bus_client",
    "completed_windows",
    "harvest_completed_windows",
    "hold",
    "maybe_heal_admit_intent_orphan",
    "probe_giw_live_hold",
    "record_shadow_pass",
    "run_shadow_for_roots",
    "tick_interval_from_env",
]
