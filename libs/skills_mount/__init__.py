"""OpenAI native skills mount — canonical bundle resolution for provider-hosted shell tools."""

from skills_mount.resolve import (
    MAX_INLINE_SKILL_BASE64_BYTES,
    ResolvedSkillBundle,
    SkillMountResolveError,
    resolve_skill_bundles,
    to_neutral_entries,
)

__all__ = [
    "MAX_INLINE_SKILL_BASE64_BYTES",
    "ResolvedSkillBundle",
    "SkillMountResolveError",
    "resolve_skill_bundles",
    "to_neutral_entries",
]
