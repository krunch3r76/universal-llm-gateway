"""Shared build-result spool — the common channel for large build-result inspection.

Backend-neutral: consumes the canonical build envelope dict (harness-neutral shape),
never a backend-specific dataclass. Shared above the harness fork line
(decision:build-result-common-channel).

Spool layout (per dispatch):
    {BUILD_RESULTS_DIR}/{dispatch_id}/
        signals.json    — bounded fast path (exit_code, status, stdout tail, failures)
        envelope.json   — full canonical envelope
        sidecar.ndjson  — copied streaming trace (copy, not symlink — decision #3)

Reachable by every seat via fs(sandbox="workspaces", op="read",
path="ulg-build-results/{dispatch_id}/signals.json") because BUILD_RESULTS_DIR
defaults under the workspaces sandbox root (/mnt/torus/projects).
"""

from __future__ import annotations

from build_results.signals import compute_signals
from build_results.spool import (
    BUILD_RESULTS_DIR,
    RETENTION_SECONDS,
    WORKSPACES_RELATIVE_ROOT,
    prune_spool,
    result_dir,
    result_ref,
    write_spool,
)

__all__ = [
    "BUILD_RESULTS_DIR",
    "RETENTION_SECONDS",
    "WORKSPACES_RELATIVE_ROOT",
    "compute_signals",
    "prune_spool",
    "result_dir",
    "result_ref",
    "write_spool",
]
