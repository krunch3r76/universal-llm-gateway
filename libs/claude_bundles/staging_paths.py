"""Out-of-tree staging for shared_sync Customize Skills renders.

Cursor indexes ``<repo>/.claude/skills/``. Shared_sync renders must not land
there (L2-b / agent-bus:5291), so staging lives outside the checkout.

The staging root must be **host-independent**: ``gen_claude_bundles.py`` renders
on whichever seat holds the repo, while the upload/status seat runs on Jupiter
over SSH. A ``$HOME``-relative root resolves to a different directory on each
host, so renders never reach the uploader. The default therefore sits on the
same NFS export that carries the checkout, at an identical path on both hosts.
There is deliberately no ``$HOME`` fallback — a silent per-host root is the
failure this default exists to prevent.

``life_local`` SOT remains ``<repo>/.claude/skills/`` — not this module.
"""

from __future__ import annotations

import os
from pathlib import Path

from claude_bundles.catalog import get_skill_catalog

_ENV_STAGING = "CLAUDE_AI_SKILLS_STAGING"
_DEFAULT_ROOT = Path("/mnt/torus/gateway/claude-ai-sync/skills")


def shared_sync_staging_root() -> Path:
    """Root directory for rendered shared_sync skill bundles (one dir per slug)."""
    override = os.environ.get(_ENV_STAGING, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_ROOT


def shared_sync_bundle_dir(slug: str) -> Path:
    """Directory containing ``SKILL.md`` for a shared_sync slug."""
    return shared_sync_staging_root() / slug


def shared_sync_skill_md(slug: str) -> Path:
    """Path to the rendered ``SKILL.md`` for a shared_sync slug."""
    return shared_sync_bundle_dir(slug) / "SKILL.md"


def life_local_skill_md(repo_root: Path, slug: str) -> Path:
    """life_local SOT path under the repo (Cursor/Claude Code local root)."""
    return repo_root / ".claude" / "skills" / slug / "SKILL.md"


def claude_ai_bundle_dir(repo_root: Path, slug: str) -> Path:
    """Bundle dir for Customize Skills upload/status: staging or life_local SOT."""
    surface = get_skill_catalog().surface_class_for(slug)
    if surface == "life_local":
        return life_local_skill_md(repo_root, slug).parent
    return shared_sync_bundle_dir(slug)
