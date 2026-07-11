"""Anthropic Skills API + claude.ai UI zip helpers for ``.claude/skills/`` bundles."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from typing import Any

import httpx

from claude_bundles.bundle_description import (
    MAX_CLAUDE_AI_DESCRIPTION_LEN,
    adapt_skill_md_for_claude_ai,
    extract_rendered_description,
    parse_frontmatter,
)

_API_BASE = "https://api.anthropic.com"
_BETA = "skills-2025-10-02"
_MAX_DESCRIPTION_LEN = MAX_CLAUDE_AI_DESCRIPTION_LEN


def default_headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": _BETA,
    }


def load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    secrets = Path.home() / ".gateway" / "secrets.env"
    if not secrets.is_file():
        return ""
    for raw in secrets.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != "ANTHROPIC_API_KEY":
            continue
        return value.strip().strip('"').strip("'")
    return ""


def validate_bundle_dir(slug: str, bundle_dir: Path) -> tuple[str, str]:
    """Return (name, description) from SKILL.md; raise ValueError on failure."""
    skill_md = bundle_dir / "SKILL.md"
    if not skill_md.is_file():
        raise ValueError(f"{slug}: missing {skill_md}")
    text = skill_md.read_text()
    fm, _ = parse_frontmatter(text)
    name = str(fm.get("name") or "").strip()
    if name != slug:
        raise ValueError(f"{slug}: frontmatter name={name!r} ≠ slug")
    description = extract_rendered_description(text)
    if not description:
        raise ValueError(f"{slug}: empty description")
    if len(description) > _MAX_DESCRIPTION_LEN:
        raise ValueError(
            f"{slug}: description len={len(description)} > {_MAX_DESCRIPTION_LEN}"
        )
    return name, description


def prepare_claude_ai_upload_md(
    skill_md: Path,
    staging_dir: Path,
    *,
    slug: str | None = None,
) -> tuple[Path, bool, int]:
    """Stage a fleet-safe copy when description exceeds the SOT ceiling (200).

    Fleet policy caps YAML ``description`` at 200 for Cursor SOT, Customize UI,
    and future Skills API inject (API/spec allow 1024; unused). See
    ``MAX_SKILL_DESCRIPTION_LEN`` and
    ``decision:claude-ai-skill-description-limits-by-surface``.

    Returns ``(upload_path, was_truncated, original_len)``.
    """
    if not skill_md.is_file():
        raise FileNotFoundError(skill_md)
    text = skill_md.read_text()
    adapted, truncated = adapt_skill_md_for_claude_ai(text)
    orig_len = len(extract_rendered_description(text))
    if not truncated:
        return skill_md, False, orig_len
    name = slug or skill_md.parent.name
    staging_dir.mkdir(parents=True, exist_ok=True)
    out = staging_dir / f"{name}.md"
    out.write_text(adapted)
    return out, True, orig_len


def render_minimal_skill_md(slug: str, description: str) -> str:
    """Frontmatter-only stub for claude.ai ``.md`` upload (name + description)."""
    from claude_bundles.resolver import _yaml_scalar

    return f"---\nname: {slug}\ndescription: {_yaml_scalar(description)}\n---\n"


def prepare_ui_upload_artifact(
    skill_md: Path,
    staging_dir: Path,
    *,
    slug: str | None = None,
    fmt: str = "md",
    full_body: bool = True,
) -> tuple[Path, int]:
    """Stage a claude.ai Customize upload file.

    ``fmt`` is ``md`` (default) or ``zip``. For ``md``, uploads the full adapted
    SKILL.md unless ``full_body=False`` (frontmatter-only stub).
    """
    if not skill_md.is_file():
        raise FileNotFoundError(skill_md)
    name = slug or skill_md.parent.name
    text = skill_md.read_text()
    adapted, _ = adapt_skill_md_for_claude_ai(text)
    desc = extract_rendered_description(adapted)
    staging_dir.mkdir(parents=True, exist_ok=True)

    if fmt == "md":
        out = staging_dir / f"{name}.md"
        body = adapted if full_body else render_minimal_skill_md(name, desc)
        out.write_text(body)
        return out, len(desc)

    bundle_dir = staging_dir / name
    bundle_dir.mkdir(parents=True, exist_ok=True)
    skill_out = bundle_dir / "SKILL.md"
    skill_out.write_text(adapted if full_body else render_minimal_skill_md(name, desc))
    zip_path = staging_dir / f"{name}.zip"
    write_skill_zip(name, bundle_dir, zip_path)
    return zip_path, len(desc)


def multipart_files(slug: str, bundle_dir: Path) -> list[tuple[str, tuple[str, bytes, str]]]:
    """Build httpx multipart tuples for ``files[]`` upload."""
    parent = bundle_dir.parent
    parts: list[tuple[str, tuple[str, bytes, str]]] = []
    for path in sorted(bundle_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(parent).as_posix()
        parts.append(("files[]", (rel, path.read_bytes(), "application/octet-stream")))
    if not parts:
        raise ValueError(f"{slug}: no files under {bundle_dir}")
    return parts


def list_custom_skills(client: httpx.Client) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": 100, "source": "custom"}
        if page:
            params["page"] = page
        resp = client.get("/v1/skills", params=params)
        resp.raise_for_status()
        payload = resp.json()
        rows.extend(payload.get("data") or [])
        if not payload.get("has_more"):
            break
        page = payload.get("next_page")
        if not page:
            break
    return rows


def latest_version_name(client: httpx.Client, skill_id: str) -> str | None:
    resp = client.get(f"/v1/skills/{skill_id}/versions", params={"limit": 1})
    resp.raise_for_status()
    data = resp.json().get("data") or []
    if not data:
        return None
    return str(data[0].get("name") or "").strip() or None


def build_slug_index(client: httpx.Client) -> dict[str, str]:
    """Map SKILL.md ``name`` → skill_id for existing custom skills."""
    index: dict[str, str] = {}
    for row in list_custom_skills(client):
        skill_id = str(row.get("id") or "")
        if not skill_id:
            continue
        name = latest_version_name(client, skill_id)
        if name:
            index[name] = skill_id
    return index


def create_skill(client: httpx.Client, files: list[tuple[str, tuple[str, bytes, str]]]) -> dict[str, Any]:
    resp = client.post("/v1/skills", files=files)
    resp.raise_for_status()
    return resp.json()


def create_skill_version(
    client: httpx.Client,
    skill_id: str,
    files: list[tuple[str, tuple[str, bytes, str]]],
) -> dict[str, Any]:
    resp = client.post(f"/v1/skills/{skill_id}/versions", files=files)
    resp.raise_for_status()
    return resp.json()


def delete_skill(client: httpx.Client, skill_id: str) -> dict[str, Any]:
    resp = client.delete(f"/v1/skills/{skill_id}")
    resp.raise_for_status()
    return resp.json()


def write_skill_zip(slug: str, bundle_dir: Path, out_path: Path) -> Path:
    """Write a claude.ai Customize-ready zip: ``{slug}/SKILL.md`` at zip root."""
    if not bundle_dir.is_dir():
        raise ValueError(f"{slug}: missing bundle dir {bundle_dir}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(bundle_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(bundle_dir.parent).as_posix())
    out_path.write_bytes(buf.getvalue())
    return out_path


def md_zip_entry_name(slug: str, *, pattern: str = "agent-skill-{slug}.md") -> str:
    """Flat zip member name — distinct, one file per skill at zip root."""
    return pattern.format(slug=slug)


def write_md_zip(
    slugs: list[str],
    bundle_root: Path,
    out_path: Path,
    *,
    name_pattern: str = "agent-skill-{slug}.md",
) -> Path:
    """Write one zip of flat ``.md`` files at a single level (no directories)."""
    if not slugs:
        raise ValueError("no slugs")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for slug in slugs:
            skill_md = bundle_root / slug / "SKILL.md"
            if not skill_md.is_file():
                raise ValueError(f"{slug}: missing {skill_md}")
            entry = md_zip_entry_name(slug, pattern=name_pattern)
            if entry in seen:
                raise ValueError(f"duplicate zip entry {entry!r} for slug {slug}")
            seen.add(entry)
            zf.writestr(entry, skill_md.read_text())
    out_path.write_bytes(buf.getvalue())
    return out_path
