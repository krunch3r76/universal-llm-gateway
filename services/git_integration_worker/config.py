"""git-integration-worker configuration — env-driven, server-owned gates."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# Diff-scoped: lint/compile only the .py files the arc introduces vs master,
# never the whole tree. A full-tree `ruff check .` can never pass for any arc
# while master carries pre-existing lint debt (965 errors at the time this was
# written), so it would block every land. P0 guard: an empty .py set must pass
# trivially — it MUST NOT fall back to `ruff check .` (that re-introduces the
# whole-tree bug). Runs in the arc worktree after master is merged into it, so
# `master...HEAD` is the arc's net change set.
_DIFF_SCOPED_GATE_SCRIPT = (
    "files=$(git diff refs/heads/master...HEAD --name-only "
    "--diff-filter=ACMR -- '*.py'); "
    'if [ -n "$files" ]; then ruff check $files && python -m compileall -q $files; fi && '
    "ruff check --select F821 services/git_integration_worker/"
)
# F821-only pass on the GIW package subtree. Whole-repo ruff is blocked by master
# lint debt; this subtree is kept F821-clean so undefined-name defects surface at
# closeout even when touched-files lint misses a path in the change set.
GIW_SUBTREE_F821_REL = "services/git_integration_worker/"
_DEFAULT_GREEN_GATE = ["bash", "-lc", _DIFF_SCOPED_GATE_SCRIPT]
_DEFAULT_SOURCE_REPO = "/mnt/torus/projects/universal-llm-gateway"
_DEFAULT_DISPATCH_WORKSPACE = str(Path(_DEFAULT_SOURCE_REPO).parent)
_DEFAULT_WORKTREE_ROOT = "/mnt/torus/projects/ulg-arc-worktrees"


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """Resolved runtime configuration for ``git-integration-worker``."""

    host: str
    port: int
    source_repo: Path
    worktree_root: Path
    dispatch_workspace: Path
    green_gate_cmd: list[str]

    @property
    def deploy_shape(self) -> str:
        return "container" if Path("/.dockerenv").exists() else "bare-metal"


def _env_path(key: str, default: str) -> Path:
    return Path(os.environ.get(key, default)).expanduser()


def _parse_green_gate_cmd() -> list[str]:
    raw = os.environ.get("GIT_INTEGRATION_GREEN_GATE_CMD", "").strip()
    if not raw:
        return list(_DEFAULT_GREEN_GATE)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ["bash", "-lc", raw]
    if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
        raise ValueError("GIT_INTEGRATION_GREEN_GATE_CMD must be a JSON string array")
    return parsed


def load_config() -> WorkerConfig:
    """Read env vars and produce a frozen :class:`WorkerConfig`."""
    return WorkerConfig(
        host=os.environ.get("GIT_INTEGRATION_WORKER_HOST", "127.0.0.1"),
        port=int(os.environ.get("GIT_INTEGRATION_WORKER_PORT", "8091")),
        source_repo=_env_path("GIT_INTEGRATION_SOURCE_REPO", _DEFAULT_SOURCE_REPO),
        worktree_root=_env_path(
            "GIT_INTEGRATION_WORKTREE_ROOT", _DEFAULT_WORKTREE_ROOT
        ),
        dispatch_workspace=_env_path(
            "GIT_INTEGRATION_DISPATCH_WORKSPACE", _DEFAULT_DISPATCH_WORKSPACE
        ),
        green_gate_cmd=_parse_green_gate_cmd(),
    )
