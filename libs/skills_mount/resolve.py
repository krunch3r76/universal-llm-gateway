"""Resolve canonical skill ids to provider-neutral inline zip mount entries."""

from __future__ import annotations

import base64
import io
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_bundles.bundle_description import parse_frontmatter
from claude_bundles.resolver import CORTEX_SOT_ROOT, render_bundle
from implement_admission.skill_source_table import (
    canonical_table_key,
    resolve_canonical_source_uri,
)

MAX_INLINE_SKILL_BASE64_BYTES = 70_254_592
_WS_PREFIX = "workspaces://universal-llm-gateway/"


class SkillMountResolveError(LookupError):
    """Skill bundle resolution failed for a caller-supplied id."""


@dataclass(frozen=True, slots=True)
class ResolvedSkillBundle:
    canonical_slug: str
    description: str
    data_base64: str


def _cortex_files_root(cortex_sot_root: Path) -> Path:
    return cortex_sot_root.parent


def _resolve_source_path(
    source_uri: str,
    *,
    cortex_sot_root: Path,
    workspaces_root: Path,
) -> Path | None:
    uri = source_uri.strip()
    if uri.startswith("agent-skills/"):
        path = _cortex_files_root(cortex_sot_root) / uri
        return path if path.is_file() else None
    if uri.startswith(_WS_PREFIX):
        rel = uri.removeprefix(_WS_PREFIX)
        path = workspaces_root / rel
        return path if path.is_file() else None
    if uri.startswith("workspaces://"):
        marker = "universal-llm-gateway/"
        if marker not in uri:
            return None
        rel = uri.split(marker, 1)[-1]
        path = workspaces_root / rel
        return path if path.is_file() else None
    return None


def _zip_skill_md(canonical_slug: str, skill_md: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{canonical_slug}/SKILL.md", skill_md)
    return buf.getvalue()


def _description_from_rendered(rendered: str, *, fallback_slug: str) -> str:
    fm, _ = parse_frontmatter(rendered)
    desc = str(fm.get("description") or "").strip()
    if desc:
        return desc
    return f"Skill bundle for {fallback_slug}"


def resolve_skill_bundles(
    skill_ids: list[str],
    *,
    cortex_sot_root: Path | None = None,
    workspaces_root: Path | None = None,
) -> list[ResolvedSkillBundle]:
    """Resolve canonical table ids to inline zip bundles for provider mount."""
    if not skill_ids:
        return []

    cortex_root = (cortex_sot_root or CORTEX_SOT_ROOT).resolve()
    ws_root = (workspaces_root or Path(__file__).resolve().parents[2]).resolve()

    bundles: list[ResolvedSkillBundle] = []
    for raw_id in skill_ids:
        skill_id = str(raw_id or "").strip()
        if not skill_id:
            raise SkillMountResolveError("empty skill id in skills= list")
        try:
            canonical_slug = canonical_table_key(skill_id)
            source_uri = resolve_canonical_source_uri(skill_id)
        except LookupError as exc:
            raise SkillMountResolveError(
                f"skill id {skill_id!r} absent from canonical table"
            ) from exc

        path = _resolve_source_path(
            source_uri,
            cortex_sot_root=cortex_root,
            workspaces_root=ws_root,
        )
        if path is None:
            raise SkillMountResolveError(
                f"skill id {skill_id!r}: source uri unmappable or unreadable "
                f"({source_uri!r})"
            )
        raw = path.read_text(encoding="utf-8", errors="replace")
        if not raw.strip():
            raise SkillMountResolveError(
                f"skill id {skill_id!r}: source file empty ({source_uri!r})"
            )

        rendered = render_bundle(canonical_slug, raw)
        fm, _ = parse_frontmatter(rendered)
        if not str(fm.get("name") or "").strip():
            raise SkillMountResolveError(
                f"skill id {skill_id!r}: rendered SKILL.md missing name frontmatter"
            )
        description = _description_from_rendered(rendered, fallback_slug=canonical_slug)
        if not description.strip():
            raise SkillMountResolveError(
                f"skill id {skill_id!r}: rendered SKILL.md missing description"
            )

        zip_bytes = _zip_skill_md(canonical_slug, rendered)
        data_base64 = base64.b64encode(zip_bytes).decode("ascii")
        if len(data_base64) > MAX_INLINE_SKILL_BASE64_BYTES:
            raise SkillMountResolveError(
                f"skill id {skill_id!r}: bundle exceeds max base64 length "
                f"({len(data_base64)} > {MAX_INLINE_SKILL_BASE64_BYTES})"
            )

        bundles.append(
            ResolvedSkillBundle(
                canonical_slug=canonical_slug,
                description=description,
                data_base64=data_base64,
            )
        )
    return bundles


def to_neutral_entries(
    bundles: list[ResolvedSkillBundle],
) -> list[dict[str, Any]]:
    """Map resolved bundles to provider-neutral ``FrontierRequest.skills_mount`` rows."""
    return [
        {
            "name": bundle.canonical_slug,
            "description": bundle.description,
            "data_base64": bundle.data_base64,
        }
        for bundle in bundles
    ]


def default_workspaces_root() -> Path:
    """Repo root used when callers do not inject ``workspaces_root``."""
    env = os.environ.get("PROJECT_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]
