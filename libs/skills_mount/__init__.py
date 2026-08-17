"""OpenAI native skills mount — canonical bundle resolution for provider-hosted shell tools."""

from skills_mount.resolve import (
    MAX_INLINE_SKILL_BASE64_BYTES,
    ResolvedSkillBundle,
    SkillMountResolveError,
    resolve_skill_bundles,
    to_neutral_entries,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('stargate',)

__all__ = [
    "MAX_INLINE_SKILL_BASE64_BYTES",
    "ResolvedSkillBundle",
    "SkillMountResolveError",
    "resolve_skill_bundles",
    "to_neutral_entries",
]
