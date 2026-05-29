"""cursorbuild — workspace-shared library for the cursor-agent dispatch surface.

Mirrors ``grokbuild`` retargeted to the ``cursor-agent`` CLI. Phase 1 lands
the pure, side-effect-light core consumed by the runner/envelope/registry
modules in later phases: the typed dispatch contracts, the argv + environment
builder, and the per-dispatch HOME isolation helpers. Re-exports the public
surface so callers import from ``cursorbuild`` rather than its submodules.
"""

from __future__ import annotations

from cursorbuild.argv import build_argv
from cursorbuild.constants import (
    CURSOR_AGENT_BIN,
    DEFAULT_MODEL,
    default_model_for_tier,
)
from cursorbuild.home import (
    CursorbuildConfigError,
    dispatch_home_path,
    setup_dispatch_home,
)
from cursorbuild.runner_types import RunnerResult, RunnerSpec

__all__ = [
    "build_argv",
    "CURSOR_AGENT_BIN",
    "DEFAULT_MODEL",
    "default_model_for_tier",
    "CursorbuildConfigError",
    "dispatch_home_path",
    "setup_dispatch_home",
    "RunnerResult",
    "RunnerSpec",
]
