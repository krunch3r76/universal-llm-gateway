"""Customize-target H1 attach checks for ``gen_claude_bundles --check``.

Callers are the bundle check script. Errors mean rendered shared_sync or
upload-adapted life_local SKILL.md would miss Cowork ``+`` → Skills attach.
"""

from __future__ import annotations

from pathlib import Path

from claude_bundles.bundle_description import adapt_skill_md_for_claude_ai
from claude_bundles.composer_skill_match import attach_h1_error
from claude_bundles.resolver import (
    life_local_slugs,
    render_bundle,
    resolve_sot,
    shared_sync_slugs,
)
from claude_bundles.staging_paths import life_local_skill_md


def collect_attach_h1_errors(
    root: Path, entity_descriptions: dict[str, str]
) -> list[str]:
    """Return attach-H1 errors for rendered shared_sync and adapted life_local.

    *entity_descriptions* feed ``render_bundle`` the same way generate does.
    """
    errors: list[str] = []
    for slug in shared_sync_slugs():
        try:
            path, _label = resolve_sot(slug, root)
        except FileNotFoundError:
            continue
        if not path.is_file():
            continue
        rendered = render_bundle(
            slug,
            path.read_text(encoding="utf-8"),
            entity_description=entity_descriptions.get(slug),
        )
        msg = attach_h1_error(slug, rendered)
        if msg:
            errors.append(msg)
    for slug in life_local_slugs():
        path = life_local_skill_md(root, slug)
        if not path.is_file():
            continue
        adapted, _ = adapt_skill_md_for_claude_ai(
            path.read_text(encoding="utf-8"), slug=slug
        )
        msg = attach_h1_error(slug, adapted)
        if msg:
            errors.append(msg)
    return errors
