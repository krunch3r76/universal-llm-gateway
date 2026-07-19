"""Out-of-tree staging for shared_sync Customize Skills renders.

Cursor indexes ``<repo>/.claude/skills/``. Shared_sync renders must not land
there (L2-b / agent-bus:5291). Staging lives under ``~/.gateway/claude-ai-sync/``.

``life_local`` SOT remains ``<repo>/.claude/skills/`` — not this module.
"""

from __future__ import annotations

import os
from pathlib import Path

from claude_bundles.catalog import get_skill_catalog

_ENV_STAGING = "CLAUDE_AI_SKILLS_STAGING"
_DEFAULT_REL = Path(".gateway") / "claude-ai-sync" / "skills"


def shared_sync_staging_root() -> Path:
    """Root directory for rendered shared_sync skill bundles (one dir per slug)."""
    override = os.environ.get(_ENV_STAGING, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / _DEFAULT_REL).resolve()


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
