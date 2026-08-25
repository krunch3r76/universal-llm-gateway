"""Parse a claude.ai container skills zip (``skills/{public,examples,user}/``).

Observed 2026-08-25 from ordinary ``/chat/`` code-exec (not Customize export,
not Platform ``/v1/skills``). Only ``user/`` is the fleet Customize library.
``public/`` and ``examples/`` are Anthropic stock — persist for CDP leverage,
never treat as catalog extras.
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

TREES = ("public", "examples", "user")


@dataclass(frozen=True)
class UserZipCompare:
    """Catalog 1:1 plan against ``skills/user/`` (stock public/examples subtracted)."""

    user: list[str]
    catalog: list[str]
    match: list[str]
    extra: list[str]
    missing: list[str]
    stale: list[str]
    stale_sizes: list[tuple[str, int, int]] = field(default_factory=list)

    def mirrored(self) -> bool:
        return not self.extra and not self.missing and not self.stale

    def as_dict(self) -> dict:
        return asdict(self)


def tree_slugs(names: list[str], tree: str) -> set[str]:
    """Unique skill directory names under ``skills/<tree>/``.

    Skips tree-root files (``manifest.json``) and dotted names.
    """
    prefix = f"skills/{tree}/"
    out: set[str] = set()
    for name in names:
        if not name.startswith(prefix) or name.endswith("/"):
            continue
        rest = name[len(prefix) :]
        slug = rest.split("/", 1)[0]
        if "/" not in rest:
            continue
        if not slug or "." in slug or slug.endswith(".skill"):
            continue
        out.add(slug)
    return out


def skill_md_key(tree: str, slug: str) -> str:
    """Return the zip namelist key for a tree's uploaded SKILL.md."""
    return f"skills/{tree}/{slug}/SKILL.md"


def zip_tree_inventory(zip_path: Path) -> dict[str, list[str]]:
    """Return sorted slugs per tree. Missing trees are empty lists."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    return {tree: sorted(tree_slugs(names, tree)) for tree in TREES}


def compare_user_zip(
    zip_path: Path,
    *,
    catalog: set[str],
    staged_bytes: dict[str, bytes],
) -> UserZipCompare:
    """Diff ``skills/user/`` vs catalog slugs and staged upload-byte hashes.

    *staged_bytes* must be the bytes ``prepare_claude_ai_upload_md`` would
    upload (via ``customize_upload_bytes``), not raw ``life_local`` SOT.
    Extras omit Anthropic stock also present under ``public/`` or ``examples/``.
    """
    catalog_l = {s.lower() for s in catalog}
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        user = tree_slugs(names, "user")
        stock = {s.lower() for t in ("public", "examples") for s in tree_slugs(names, t)}
        extra = sorted(s for s in user if s.lower() not in catalog_l and s.lower() not in stock)
        missing = sorted(s for s in catalog_l if s not in user)
        match: list[str] = []
        stale: list[str] = []
        stale_sizes: list[tuple[str, int, int]] = []
        for slug in sorted(catalog_l):
            key = skill_md_key("user", slug)
            if key not in names:
                continue
            zb = zf.read(key)
            local = staged_bytes.get(slug)
            if local is None:
                stale.append(slug)
                stale_sizes.append((slug, len(zb), -1))
                continue
            if hashlib.sha256(zb).hexdigest() == hashlib.sha256(local).hexdigest():
                match.append(slug)
            else:
                stale.append(slug)
                stale_sizes.append((slug, len(zb), len(local)))
    return UserZipCompare(
        user=sorted(user),
        catalog=sorted(catalog_l),
        match=match,
        extra=extra,
        missing=missing,
        stale=stale,
        stale_sizes=stale_sizes,
    )


def pick_download_label(labels: list[str]) -> str | None:
    """Prefer the in-chat ``Download Claude skills`` card over a generic Download."""
    lowered = [(label, label.lower()) for label in labels if label and label.strip()]
    for label, low in lowered:
        if "claude skills" in low and "download" in low:
            return label
    for label, low in lowered:
        if "download" in low and "skill" in low:
            return label
    return None
