"""Resolve agent_skill source_uri → implement-packet fs load lines."""

from __future__ import annotations

from typing import Any

_WS = "workspaces://universal-llm-gateway"

# Offline / entity_get-failure fallback for consolidated SOT paths (Track A).
_KNOWN_SKILL_SOURCE_URIS: dict[str, str] = {
    "architecture-invariants": f"{_WS}/docs/agent-guides/skills/architecture-invariants.md",
    "ulg-architecture": f"{_WS}/docs/agent-guides/skills/ulg-architecture.md",
    "git-posture": f"{_WS}/docs/agent-guides/skills/git-posture.md",
    "service-lifecycle": f"{_WS}/.cursor/skills/service-lifecycle/SKILL.md",
}


def _normalize_source_uri(source_uri: str) -> str:
    uri = source_uri.strip().removeprefix("files://")
    if uri.startswith("cortex://"):
        uri = uri.removeprefix("cortex://")
    marker = "/agent-skills/"
    if marker in uri:
        return f"agent-skills/{uri.split(marker, 1)[1].lstrip('/')}"
    return uri


def source_uri_to_fs_line(source_uri: str, *, op: str = "md_read") -> str:
    """Translate agent_skill.source_uri to an implement-packet fs load line."""
    uri = _normalize_source_uri(source_uri)
    if uri.startswith("workspaces://"):
        path = uri.removeprefix("workspaces://")
        return f'fs(sandbox="workspaces", op="{op}", path="{path}")'
    if uri.startswith(("universal-llm-gateway/", "projects/")):
        return f'fs(sandbox="workspaces", op="{op}", path="{uri}")'
    if uri.startswith("agent-skills/"):
        return f'fs(sandbox="cortex", op="{op}", path="{uri}")'
    if "/" not in uri:
        stem = uri[:-3] if uri.endswith(".md") else uri
        return f'fs(sandbox="cortex", op="{op}", path="agent-skills/{stem}.md")'
    raise ValueError(f"unsupported source_uri: {source_uri!r}")


def _entity_source_uri(entity: dict[str, Any]) -> str | None:
    top = entity.get("source_uri")
    if top and str(top).strip():
        return str(top).strip()
    attrs = entity.get("attributes") or {}
    if not isinstance(attrs, dict):
        return None
    raw = attrs.get("source_uri")
    return str(raw).strip() if raw else None


def resolve_skill_source_uri(slug: str) -> str:
    """Resolve slug → source_uri; entity_get when available, else known map, else cortex default."""
    try:
        from implement_admission.closeout_runtime import get_runtime

        resp = get_runtime().dispatch(
            "entity_get", {"entity_id": f"agent_skill:{slug}"}
        )
        if isinstance(resp, dict) and "error" not in resp:
            resolved = _entity_source_uri(resp)
            if resolved:
                return resolved
    except Exception:
        pass
    if slug in _KNOWN_SKILL_SOURCE_URIS:
        return _KNOWN_SKILL_SOURCE_URIS[slug]
    return f"agent-skills/{slug}.md"


def skill_slug_to_fs_line(slug: str, *, op: str = "md_read") -> str:
    return source_uri_to_fs_line(resolve_skill_source_uri(slug), op=op)
