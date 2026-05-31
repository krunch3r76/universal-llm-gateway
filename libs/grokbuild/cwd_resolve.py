"""Resolve a caller-supplied cwd / repo-name into a host absolute path.

The MCP fs sandbox reports container paths (/data/project/<repo>/...), but the
grokbuild-worker runs on the host and operates on /mnt/torus/projects/<repo>.
Callers should not have to know the bind-mount mapping; this resolver owns it.
"""

from __future__ import annotations

import os

# Single host projects root — same constant the worktree validator uses.
PROJECTS_ROOT = os.getenv("GROKBUILD_PROJECTS_ROOT", "/mnt/torus/projects")
# Container prefix the fs sandbox reports; rewritten to PROJECTS_ROOT.
_CONTAINER_PREFIX = os.getenv("GROKBUILD_CONTAINER_PROJECTS", "/data/project")


def resolve_cwd(cwd: str | None, source_repo: str | None) -> tuple[str, str]:
    """Return (resolved_abs_path, reason). reason is empty on success.

    Precedence:
      1. source_repo (bare name → PROJECTS_ROOT/name; abs path → as-is).
      2. cwd, with a /data/project/... → PROJECTS_ROOT/... rewrite.
    Does NOT check existence — the validator's isdir gate still owns that, so
    the reject message remains the canonical one.
    """
    if source_repo:
        if "/" not in source_repo:
            return os.path.join(PROJECTS_ROOT, source_repo), ""
        return os.path.realpath(source_repo), ""
    if cwd:
        if cwd.startswith(_CONTAINER_PREFIX + "/") or cwd == _CONTAINER_PREFIX:
            rewritten = PROJECTS_ROOT + cwd[len(_CONTAINER_PREFIX) :]
            return rewritten, ""
        return cwd, ""
    return "", "one of cwd or source_repo is required"
