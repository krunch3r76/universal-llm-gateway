"""Resolve agent_skill source_uri → implement-packet fs load lines."""

from __future__ import annotations

from typing import Literal

from implement_admission.skill_source_table import (
    SkillSourceResolveError,
    resolve_canonical_source_uri,
)


def _normalize_source_uri(source_uri: str) -> str:
    uri = source_uri.strip().removeprefix("files://")
    if uri.startswith("cortex://"):
        uri = uri.removeprefix("cortex://")
    marker = "/agent-skills/"
    if marker in uri:
        return f"agent-skills/{uri.split(marker, 1)[1].lstrip('/')}"
    return uri


def source_uri_to_fs_line(
    source_uri: str,
    *,
    op: str = "md_read",
    fs_call_style: Literal["sandbox_kwargs", "positional"] = "sandbox_kwargs",
) -> str:
    """Translate agent_skill.source_uri to an implement-packet fs load line."""
    uri = _normalize_source_uri(source_uri)
    positional = fs_call_style == "positional"

    def _workspaces_line(path: str) -> str:
        if positional:
            return f'fs(workspaces, op={op}, path="{path}")'
        return f'fs(sandbox="workspaces", op="{op}", path="{path}")'

    def _cortex_line(path: str) -> str:
        if positional:
            return f'fs(cortex, op={op}, path="{path}")'
        return f'fs(sandbox="cortex", op="{op}", path="{path}")'

    if uri.startswith("workspaces://"):
        return _workspaces_line(uri.removeprefix("workspaces://"))
    if uri.startswith(("universal-llm-gateway/", "projects/")):
        return _workspaces_line(uri)
    if uri.startswith("agent-skills/"):
        return _cortex_line(uri)
    if uri.startswith(".cursor/skills/"):
        return _workspaces_line(f"universal-llm-gateway/{uri}")
    if "/" not in uri:
        stem = uri[:-3] if uri.endswith(".md") else uri
        return _cortex_line(f"agent-skills/{stem}.md")
    raise ValueError(f"unsupported source_uri: {source_uri!r}")


def resolve_skill_source_uri(slug_or_entity_id: str) -> str:
    """Resolve slug/entity id → source_uri via committed D1 table (fail-loud)."""
    return resolve_canonical_source_uri(slug_or_entity_id)


def skill_slug_to_fs_line(slug_or_entity_id: str, *, op: str = "md_read") -> str:
    return source_uri_to_fs_line(resolve_skill_source_uri(slug_or_entity_id), op=op)


__all__ = [
    "SkillSourceResolveError",
    "resolve_skill_source_uri",
    "skill_slug_to_fs_line",
    "source_uri_to_fs_line",
]
