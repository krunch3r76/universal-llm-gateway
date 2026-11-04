"""Section-scoped skill load plans for house resume, hop, and liaison event paths."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from claude_bundles.catalog import load_skill_catalog
from markdown_sections import list_sections

OperationKind = Literal["house_resume", "liaison_event", "checkpoint_hop", "implement"]

_CORE_SLUGS: tuple[str, ...] = ("reasoning-posture", "lean-dispatch")

_OPERATION_SECTIONS: dict[OperationKind, dict[str, tuple[str, ...]]] = {
    "house_resume": {
        "ide-continuity": ("Impresario — the tab that runs the house", "Registers", "FOL pipeline"),
        "checkpoint-discipline": (
            "Resume pickup stack",
            "Operator-facing",
        ),
    },
    "liaison_event": {
        "ide-continuity": ("Gear inference (Light / Attended / Overnight)", "FOL pipeline"),
    },
    "checkpoint_hop": {
        "checkpoint-discipline": ("CHECKPOINT fire (lean)", "Resume pickup stack"),
        "ide-continuity": ("Registers",),
    },
    "implement": {
        "ide-continuity": ("Play (binding)",),
        "checkpoint-discipline": ("Posting a RULING between checkpoints",),
    },
}


@dataclass(frozen=True, slots=True)
class SkillSectionLoad:
    slug: str
    section: str
    path: str
    read_sha256: str | None
    fs_call: str


def _skill_path(slug: str) -> Path | None:
    catalog = load_skill_catalog(validate_sot=False)
    try:
        entry = catalog.get(slug)
    except KeyError:
        return None
    root = Path(__file__).resolve().parents[2]
    dirname = entry.sot_dirname or slug
    for base in (
        root / "cursor-plugins/ulg-ecosystem/skills",
        root / ".cursor/skills",
    ):
        path = base / dirname / "SKILL.md"
        if path.is_file():
            return path
    return None


def _section_exists(text: str, section: str) -> bool:
    try:
        sections = list_sections(text)
    except Exception:  # noqa: BLE001
        return False
    want = section.strip().casefold()
    for row in sections:
        heading = str(row.get("heading") or row.get("path") or "")
        if heading.casefold() == want or str(row.get("path") or "").casefold() == want:
            return True
    return False


def build_skill_section_plan(
    operation: OperationKind,
    *,
    already_loaded: set[tuple[str, str]] | None = None,
) -> list[SkillSectionLoad]:
    """Ordered section loads for *operation*; skips (slug, section) in *already_loaded*."""
    loaded = already_loaded or set()
    plan: list[SkillSectionLoad] = []
    spec = _OPERATION_SECTIONS.get(operation, {})
    slugs = list(_CORE_SLUGS)
    for slug in spec:
        if slug not in slugs:
            slugs.append(slug)
    for slug in slugs:
        sections = spec.get(slug)
        if not sections:
            if (slug, "__full__") in loaded:
                continue
            path = _skill_path(slug)
            if path is None:
                plan.append(
                    SkillSectionLoad(
                        slug=slug,
                        section="",
                        path=f"<missing:{slug}>",
                        read_sha256=None,
                        fs_call=f'Use the `{slug}` skill (path missing — refresh catalog)',
                    )
                )
                continue
            text = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            rel = f"cursor-plugins/ulg-ecosystem/skills/{slug}/SKILL.md"
            plan.append(
                SkillSectionLoad(
                    slug=slug,
                    section="",
                    path=rel,
                    read_sha256=digest,
                    fs_call=(
                        f'fs(op="md_list", sandbox="workspaces", '
                        f'path="universal-llm-gateway/{rel}") then md_read required sections'
                    ),
                )
            )
            continue
        path = _skill_path(slug)
        if path is None:
            continue
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rel = f"cursor-plugins/ulg-ecosystem/skills/{slug}/SKILL.md"
        for section in sections:
            key = (slug, section)
            if key in loaded:
                continue
            if not _section_exists(text, section):
                plan.append(
                    SkillSectionLoad(
                        slug=slug,
                        section=section,
                        path=rel,
                        read_sha256=digest,
                        fs_call=(
                            f'section "{section}" missing/renamed in {slug} — '
                            f'fs(op="md_list", sandbox="workspaces", '
                            f'path="universal-llm-gateway/{rel}") then md_read'
                        ),
                    )
                )
                continue
            plan.append(
                SkillSectionLoad(
                    slug=slug,
                    section=section,
                    path=rel,
                    read_sha256=digest,
                    fs_call=(
                        f'fs(op="md_read", sandbox="workspaces", '
                        f'path="universal-llm-gateway/{rel}", section="{section}") '
                        f"read_sha256={digest[:12]}"
                    ),
                )
            )
    return plan


def format_skill_load_block(
    operation: OperationKind,
    *,
    already_loaded: set[tuple[str, str]] | None = None,
) -> str:
    lines = [
        f"SKILL SECTIONS ({operation}): load core + operation sections via md_list/md_read — not full-file skim.",
    ]
    for row in build_skill_section_plan(operation, already_loaded=already_loaded):
        lines.append(row.fs_call)
    return "\n".join(lines) + "\n"


__all__ = [
    "OperationKind",
    "SkillSectionLoad",
    "build_skill_section_plan",
    "format_skill_load_block",
]
