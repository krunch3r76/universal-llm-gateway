"""Upload-byte authority for Customize Skills recon.

Recon must hash the bytes ``prepare_claude_ai_upload_md`` would upload, not raw
``life_local`` SOT. Shared_sync reads staged render then adapt; life_local reads
SOT then adapt through the same uploader path.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from claude_bundles.resolver import claude_ai_target_slugs
from claude_bundles.skills_api import prepare_claude_ai_upload_md
from claude_bundles.staging_paths import claude_ai_bundle_dir


def customize_upload_bytes(repo: Path, slug: str) -> bytes:
    """Return exactly the bytes Customize upload would send for *slug*."""
    skill_md = claude_ai_bundle_dir(repo, slug) / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(skill_md)
    with tempfile.TemporaryDirectory(prefix="customize-upload-bytes-") as td:
        upload_path, _, _ = prepare_claude_ai_upload_md(
            skill_md, Path(td), slug=slug
        )
        return upload_path.read_bytes()


def build_staged_bytes_map(
    repo: Path, *, slugs: list[str] | None = None
) -> dict[str, bytes]:
    """Map each lowercased slug to the bytes Customize upload would send."""
    targets = slugs if slugs is not None else claude_ai_target_slugs()
    out: dict[str, bytes] = {}
    for slug in targets:
        try:
            out[slug.lower()] = customize_upload_bytes(repo, slug)
        except FileNotFoundError:
            continue
    return out
