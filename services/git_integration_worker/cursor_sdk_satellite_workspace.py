"""Resolve per-dispatch git identity for cursor-sdk ``workspace=`` wire param.

GIW admit calls ``resolve_dispatch_source_repo`` so capture, land lease, and
Gate D use the named satellite git while control-plane paths stay on hub ULG.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_HUB_REPO_NAME = "universal-llm-gateway"
_SATELLITES_REL = Path("cursor-plugins/ulg-ecosystem/SATELLITES.txt")


class CursorWorkspaceError(Exception):
    """Base for workspace resolution failures surfaced as 422 at SDK admit."""

    code: str = "CURSOR_WORKSPACE_INVALID"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CursorWorkspaceInvalid(CursorWorkspaceError):
    """Malformed ``workspace`` token (empty, path separators, traversal)."""

    code = "CURSOR_WORKSPACE_INVALID"


class CursorWorkspaceUnknown(CursorWorkspaceError):
    """Name absent from hub ``SATELLITES.txt`` allowlist roster."""

    code = "CURSOR_WORKSPACE_UNKNOWN"


class CursorWorkspaceNotGit(CursorWorkspaceError):
    """Allowlisted target exists but has no ``.git`` directory."""

    code = "CURSOR_WORKSPACE_NOT_GIT"


class CursorWorkspaceHubUseOmit(CursorWorkspaceError):
    """Hub repo name must be omitted — pass no ``workspace`` for ULG identity."""

    code = "CURSOR_WORKSPACE_HUB_USE_OMIT"


def load_satellite_allowlist(*, hub: Path) -> frozenset[str]:
    """Load allowlisted satellite directory names from hub ``SATELLITES.txt``.

    Skips blank lines and ``#`` comments. Hub itself is never listed in the
    roster file — omit ``workspace`` to select it.
    """
    roster_path = (hub / _SATELLITES_REL).resolve()
    if not roster_path.is_file():
        return frozenset()
    names: set[str] = set()
    for line in roster_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        names.add(stripped)
    return frozenset(names)


def _hub_resolved(hub: Path) -> Path:
    return Path(hub).resolve()


def _source_is_hub(source_repo: str | None, hub: Path) -> bool:
    """True when *source_repo* is this GIW install's hub, including via symlink."""
    if not source_repo:
        return False
    return Path(str(source_repo)).resolve() == _hub_resolved(hub)


def recorded_workspace(
    *,
    record: dict[str, Any] | None = None,
    record_json: str | None = None,
    source_repo: str | None = None,
    hub: Path,
) -> str | None:
    """Satellite ``workspace=`` token stored on a parent, or derived from its git.

    Hub ULG is omitted (``None``): identity is ``Path(source_repo).resolve()
    == hub.resolve()``, not the directory name ``universal-llm-gateway``.
    A stored token equal to the hub's resolved basename is also omitted.
    Legacy park rows may lack ``record_json.workspace`` and still carry a
    satellite ``source_repo`` column — use the path basename then.
    """
    hub_resolved = _hub_resolved(hub)
    data = record
    if data is None:
        try:
            parsed = json.loads(record_json or "{}")
        except json.JSONDecodeError:
            parsed = {}
        data = parsed if isinstance(parsed, dict) else {}
    raw = data.get("workspace")
    token = raw.strip() if isinstance(raw, str) else ""
    if token and token != hub_resolved.name:
        return token
    if _source_is_hub(source_repo, hub_resolved):
        return None
    if not source_repo:
        return None
    name = Path(str(source_repo)).name
    if not name or name == hub_resolved.name:
        return None
    return name


def resolve_dispatch_source_repo(
    workspace: str | None,
    *,
    hub: Path,
    projects_root: Path,
    allowlist: frozenset[str] | None = None,
) -> Path:
    """Return the git repo root for capture/land/Gate D for this dispatch.

    ``None``/omit → ``hub``. Named allowlist satellites resolve to
    ``{projects_root}/{name}`` when that path is a git checkout.
    """
    if workspace is None or not str(workspace).strip():
        return hub.resolve()

    name = str(workspace).strip()
    if not name:
        raise CursorWorkspaceInvalid("workspace must be a non-empty allowlist name")
    if "/" in name or "\\" in name or ".." in Path(name).parts:
        raise CursorWorkspaceInvalid(
            f"workspace must be a single allowlist name, not a path: {name!r}"
        )
    if name == _HUB_REPO_NAME:
        raise CursorWorkspaceHubUseOmit(
            "workspace=universal-llm-gateway is invalid; omit workspace for hub ULG"
        )

    roster = allowlist if allowlist is not None else load_satellite_allowlist(hub=hub)
    if name not in roster:
        raise CursorWorkspaceUnknown(
            f"workspace {name!r} is not in the satellite allowlist"
        )

    target = (projects_root / name).resolve()
    git_dir = target / ".git"
    if not git_dir.exists():
        raise CursorWorkspaceNotGit(
            f"workspace {name!r} is not a git checkout at {target}"
        )
    return target


__all__ = [
    "CursorWorkspaceError",
    "CursorWorkspaceHubUseOmit",
    "CursorWorkspaceInvalid",
    "CursorWorkspaceNotGit",
    "CursorWorkspaceUnknown",
    "load_satellite_allowlist",
    "recorded_workspace",
    "resolve_dispatch_source_repo",
]
