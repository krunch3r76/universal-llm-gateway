"""Skill mount resolution — provider bundles (base64 zip) and Cursor filesystem SoTs."""

from skills_mount.cursor_fs import (
    CursorSkillResolution,
    CursorSkillSot,
    CursorSkillSotError,
    classify_cursor_skills,
    resolve_cursor_skill_sot,
)
from skills_mount.resolve import (
    MAX_INLINE_SKILL_BASE64_BYTES,
    ResolvedSkillBundle,
    SkillMountResolveError,
    resolve_skill_bundles,
    to_neutral_entries,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('stargate', 'git_integration_worker')

__all__ = [
    "MAX_INLINE_SKILL_BASE64_BYTES",
    "CursorSkillResolution",
    "CursorSkillSot",
    "CursorSkillSotError",
    "ResolvedSkillBundle",
    "SkillMountResolveError",
    "classify_cursor_skills",
    "resolve_cursor_skill_sot",
    "resolve_skill_bundles",
    "to_neutral_entries",
]
