"""Logical guidance resource keys for delivery-audit token locality."""

from __future__ import annotations

import re
from pathlib import Path

VALID_PROJECTION_SURFACES = frozenset(
    {
        "cortex",
        "workspaces_doc",
        "generated_rule",
        "generated_skill",
        "boot_card_block",
        "tool_descriptor",
        "mcp_schema",
        "provider_affordance_surface",
        "sidecar_corpus",
    }
)

_CLASS_QUALIFIED_RESOURCE_CLASSES = frozenset(
    {
        "boot-card",
        "tool-descriptor",
        "mcp-schema",
        "provider-affordance",
    }
)


def _strip_uri_prefix(value: str) -> str:
    if value.startswith("cortex://"):
        return value.removeprefix("cortex://")
    if value.startswith("workspaces://"):
        path = value.removeprefix("workspaces://")
        return path.removeprefix("universal-llm-gateway/")
    return value


def _split_fragment(value: str) -> tuple[str, str | None]:
    path, separator, fragment = value.partition("#")
    return path, fragment if separator else None


def _normalize_anchor(value: str) -> str:
    anchor = value.strip().lstrip("#").lower()
    anchor = re.sub(r"[^a-z0-9]+", "-", anchor)
    return anchor.strip("-")


def _guidance_slug_from_path(path: str) -> str:
    clean_path = _strip_uri_prefix(path).strip("/")
    if clean_path.endswith("/SKILL.md"):
        return Path(clean_path).parent.name
    return Path(clean_path).stem


def guidance_resource_key(slug: str, section: str | None = None) -> str:
    """Return the logical guidance key used for token-locality deduplication."""
    clean_slug = slug.strip()
    if not clean_slug:
        raise ValueError("slug is required")
    key = f"guidance:{clean_slug}"
    if section:
        anchor = _normalize_anchor(section)
        if anchor:
            key = f"{key}#{anchor}"
    return key


def class_qualified_guidance_resource_key(resource_class: str, name: str) -> str:
    """Return a reserved logical key for a non-document guidance surface."""
    if resource_class not in _CLASS_QUALIFIED_RESOURCE_CLASSES:
        raise ValueError(f"unknown guidance resource class: {resource_class!r}")
    anchor = _normalize_anchor(name)
    if not anchor:
        raise ValueError("name is required")
    return f"guidance:{resource_class}#{anchor}"


def derive_guidance_resource_key(
    *,
    artifact_class: str,
    artifact_id: str,
    projection_surface: str | None = None,
    affordance_kind: str | None = None,
) -> str:
    """Derive the logical guidance key from a delivered-artifact identity."""
    if artifact_class in ("http_rule_body", "http_skill_body"):
        path, section = _split_fragment(artifact_id)
        return guidance_resource_key(_guidance_slug_from_path(path), section)
    if artifact_class == "tool_fol_descriptor":
        resource_class = (
            "mcp-schema" if projection_surface == "mcp_schema" else "tool-descriptor"
        )
        return class_qualified_guidance_resource_key(resource_class, artifact_id)
    if artifact_class == "provider_affordance_surface":
        return class_qualified_guidance_resource_key(
            "provider-affordance",
            affordance_kind or artifact_id,
        )
    if artifact_class == "boot_card_block":
        return class_qualified_guidance_resource_key("boot-card", artifact_id)
    raise ValueError(f"unknown artifact class: {artifact_class!r}")


def guidance_projection_surface(
    *,
    artifact_class: str,
    source_uri: str | None = None,
    tool_surface: str | None = None,
) -> str:
    """Map a delivery source to the provenance surface stored per child row."""
    if source_uri:
        if source_uri.startswith("cortex://"):
            return "cortex"
        if source_uri.startswith("workspaces://"):
            return "workspaces_doc"
    if artifact_class == "http_rule_body":
        return "generated_rule"
    if artifact_class == "http_skill_body":
        return "generated_skill"
    if artifact_class == "boot_card_block":
        return "boot_card_block"
    if artifact_class == "tool_fol_descriptor":
        return "mcp_schema" if tool_surface == "mcp_schema" else "tool_descriptor"
    if artifact_class == "provider_affordance_surface":
        return "provider_affordance_surface"
    raise ValueError(f"unknown artifact class: {artifact_class!r}")
