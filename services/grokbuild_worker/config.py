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
    # Agent-bus notification config (Phase 2).
    # ∀ agent_bus_token == "": notifier is disabled (no-op).
    agent_bus_url: str
    agent_bus_token: str
    grok_auth_notify_slug: (
        str  # review §F2: slug for /threads/with-turn, NOT a thread id
    )
    grok_auth_notify_to: str
    grok_auth_debounce_h: int
    # Cortex integration (Phase 3 — optional).  Token empty = disabled.
    cortex_api_url: str
    cortex_api_token: str

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
        # Agent-bus notification (Phase 2).  Token empty = notifier disabled.
        agent_bus_url=os.environ.get(
            "AGENT_BUS_URL",
            "unix:///tmp/universal-protocol/agent-bus.sock",
        ),
        agent_bus_token=os.environ.get("AGENT_BUS_TOKEN", ""),
        grok_auth_notify_slug=os.environ.get("GROK_AUTH_NOTIFY_SLUG", "grokbuild-auth"),
        grok_auth_notify_to=os.environ.get("GROK_AUTH_NOTIFY_TO", "web"),
        grok_auth_debounce_h=int(os.environ.get("GROKBUILD_AUTH_DEBOUNCE_H", "4")),
        cortex_api_url=os.environ.get(
            "CORTEX_API_URL",
            "unix:///tmp/universal-protocol/cortex-api.sock",
        ),
        cortex_api_token=os.environ.get("CORTEX_API_TOKEN", ""),
    )
