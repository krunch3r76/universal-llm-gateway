"""grokbuild-worker configuration — env-driven Pydantic settings.

Operator-locked decisions encoded here:

* Sidecar dir default: ``~/.local/share/grokbuild-worker/sidecars/`` (XDG,
  user-writable, no root required).  Docker deployments set
  ``GROKBUILD_SIDECAR_DIR`` explicitly to keep the bind-mounted
  ``/var/lib/grokbuild-worker/sidecars`` path.
* Registry path default: ``~/.local/share/grokbuild-worker/registry.json``
  — same XDG root.
* No ``GROKBUILD_AUTH_TOKEN`` — Stargate enforces auth at its edge and
  the worker trusts requests arriving from Stargate (same pattern as
  ``cortex-api`` / ``agent-bus``).
* Build-result spool dir is owned by ``libs/build_results`` (env
  ``BUILD_RESULTS_DIR``, default ``/mnt/torus/projects/ulg-build-results``),
  NOT by ``WorkerConfig`` — it is shared with cursorbuild above the
  grokbuild/cursorbuild fork line, so it is intentionally not duplicated here.

``GROKBUILD_SIDECAR_DIR`` / ``GROKBUILD_REGISTRY_PATH`` env var overrides
always take precedence over the defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """Resolved runtime configuration for ``grokbuild-worker``."""

    host: str
    port: int
    sidecar_dir: Path
    registry_path: Path
    grok_bin_path: Path
    grok_auth_dir: Path
    projects_root: Path

    @property
    def deploy_shape(self) -> str:
        """Best-effort deploy-shape hint (``container`` if /.dockerenv else ``bare-metal``)."""
        return "container" if Path("/.dockerenv").exists() else "bare-metal"


def _env_path(key: str, default: str) -> Path:
    """Return ``Path`` from env var ``key`` with expanduser, falling back to ``default``."""
    raw = os.environ.get(key, default)
    return Path(raw).expanduser()


def load_config() -> WorkerConfig:
    """Read env vars and produce a frozen :class:`WorkerConfig`."""
    return WorkerConfig(
        host=os.environ.get("GROKBUILD_WORKER_HOST", "127.0.0.1"),
        port=int(os.environ.get("GROKBUILD_WORKER_PORT", "8090")),
        sidecar_dir=_env_path(
            "GROKBUILD_SIDECAR_DIR", "~/.local/share/grokbuild-worker/sidecars"
        ),
        registry_path=_env_path(
            "GROKBUILD_REGISTRY_PATH", "~/.local/share/grokbuild-worker/registry.json"
        ),
        grok_bin_path=_env_path("GROK_BIN_PATH", "/home/io/.local/bin/grok"),
        grok_auth_dir=_env_path("GROK_AUTH_DIR", "/home/io/.grok"),
        projects_root=_env_path("PROJECTS_ROOT", "/mnt/torus/projects"),
    )
