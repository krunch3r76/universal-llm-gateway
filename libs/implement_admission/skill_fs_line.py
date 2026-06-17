"""Resolve agent_skill source_uri → implement-packet fs load lines."""

from __future__ import annotations

import functools
from typing import Any, Literal

from universal_logging import get_logger

logger = get_logger(__name__)

_WS = "workspaces://universal-llm-gateway"

# Offline / entity_get-failure fallback for consolidated SOT paths (Track A).
# Covers every CODING_SESSION_BUNDLE inject+advertise slug whose canonical
# source_uri is workspaces-resident (entity_get verified 2026-06-17).
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
    if "/" not in uri:
        stem = uri[:-3] if uri.endswith(".md") else uri
        return _cortex_line(f"agent-skills/{stem}.md")
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


@functools.lru_cache(maxsize=None)  # noqa: UP033 — contract requires lru_cache form
def resolve_skill_source_uri(slug: str) -> str:
    """Resolve slug → source_uri. Static map is the deterministic SOT; entity_get is a
    discovery fallback only for slugs absent from the map (never on the render hot path
    for mapped/bundle slugs)."""
    if slug in _KNOWN_SKILL_SOURCE_URIS:
        return _KNOWN_SKILL_SOURCE_URIS[slug]
    try:
        from implement_admission.closeout_runtime import get_runtime
    except ImportError:
        return f"agent-skills/{slug}.md"
    try:
        resp = get_runtime().dispatch(
            "entity_get", {"entity_id": f"agent_skill:{slug}"}
        )
        if isinstance(resp, dict) and "error" not in resp:
            resolved = _entity_source_uri(resp)
            if resolved:
                return resolved
    except Exception as exc:
        logger.warning("skill source_uri entity_get failed slug=%s error=%s", slug, exc)
    return f"agent-skills/{slug}.md"


def skill_slug_to_fs_line(slug: str, *, op: str = "md_read") -> str:
    return source_uri_to_fs_line(resolve_skill_source_uri(slug), op=op)
