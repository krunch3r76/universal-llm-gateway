"""Section-scoped skill load plans for house resume, hop, and liaison event paths."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from claude_bundles.catalog import load_skill_catalog
from markdown_sections import list_sections, read_section

OperationKind = Literal["house_resume", "liaison_event", "checkpoint_hop", "implement"]

LoadedKey = tuple[str, str, str]  # slug, section_path, file_sha256

_CORE_REQUIRED: dict[str, tuple[str, ...]] = {
    "reasoning-posture": ("Before acting",),
    "lean-dispatch": ("Packet in / envelope out",),
}

_OPERATION_SECTIONS: dict[OperationKind, dict[str, tuple[str, ...]]] = {
    "house_resume": {
        "ide-continuity": (
            "Startup section load (house paths)",
            "Registers",
            "FOL pipeline",
        ),
        "checkpoint-discipline": (
            "When to load",
            "Resume (lean default)",
        ),
    },
    "liaison_event": {
        "ide-continuity": ("Gear inference (Light / Attended / Overnight)", "FOL pipeline"),
    },
    "checkpoint_hop": {
        "checkpoint-discipline": ("Utterance", "Resume (lean default)"),
        "ide-continuity": ("Startup section load (house paths)", "Registers"),
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
    body_utf8_bytes: int
    fs_call: str
    status: str  # ok | missing_file | missing_section | stale_skip


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _skill_repo_rel(slug: str, dirname: str) -> str:
    return f"cursor-plugins/ulg-ecosystem/skills/{dirname}/SKILL.md"


def _skill_path(slug: str, dirname: str) -> Path | None:
    root = _repo_root()
    for base in (
        root / "cursor-plugins/ulg-ecosystem/skills",
        root / ".cursor/skills",
    ):
        path = base / dirname / "SKILL.md"
        if path.is_file():
            return path
    return None


def _resolve_section_path(text: str, section: str) -> str | None:
    want = section.strip().casefold()
    try:
        sections = list_sections(text)
    except Exception:  # noqa: BLE001
        return None
    for row in sections:
        heading = str(row.get("heading") or "")
        path = str(row.get("path") or heading)
        if heading.casefold() == want or path.casefold() == want:
            return path
    return None


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def _load_key(slug: str, section_path: str, file_sha: str) -> LoadedKey:
    return (slug, section_path, file_sha)


def _is_loaded(
    slug: str, section_path: str, file_sha: str, already_loaded: set[LoadedKey] | None
) -> bool:
    if not already_loaded:
        return False
    for key in already_loaded:
        if key[0] != slug or key[1] != section_path:
            continue
        if key[2] == file_sha:
            return True
    return False


def _append_load(
    plan: list[SkillSectionLoad],
    *,
    slug: str,
    section: str,
    rel: str,
    file_sha: str,
    text: str,
    already_loaded: set[LoadedKey] | None,
) -> None:
    section_path = _resolve_section_path(text, section)
    if section_path is None:
        plan.append(
            SkillSectionLoad(
                slug=slug,
                section=section,
                path=rel,
                read_sha256=file_sha,
                body_utf8_bytes=0,
                status="missing_section",
                fs_call=(
                    f'REQUIRED section missing — fs(op="md_list", sandbox="workspaces", '
                    f'path="universal-llm-gateway/{rel}") then md_read renamed section'
                ),
            )
        )
        return
    if _is_loaded(slug, section_path, file_sha, already_loaded):
        plan.append(
            SkillSectionLoad(
                slug=slug,
                section=section_path,
                path=rel,
                read_sha256=file_sha,
                body_utf8_bytes=0,
                status="stale_skip",
                fs_call=(
                    f"skip {slug} §{section_path} — already loaded at read_sha256={file_sha[:12]}"
                ),
            )
        )
        return
    body = read_section(text, section_path)
    body_bytes = len(body.encode("utf-8"))
    plan.append(
        SkillSectionLoad(
            slug=slug,
            section=section_path,
            path=rel,
            read_sha256=file_sha,
            body_utf8_bytes=body_bytes,
            status="ok",
            fs_call=(
                f'fs(op="md_read", sandbox="workspaces", '
                f'path="universal-llm-gateway/{rel}", section="{section_path}") '
                f"read_sha256={file_sha[:12]}"
            ),
        )
    )


def build_skill_section_plan(
    operation: OperationKind,
    *,
    already_loaded: set[LoadedKey] | None = None,
) -> list[SkillSectionLoad]:
    """Executable core + operation section loads."""
    catalog = load_skill_catalog(validate_sot=False)
    spec = _OPERATION_SECTIONS.get(operation, {})
    slugs_ordered: list[str] = []
    for slug in _CORE_REQUIRED:
        slugs_ordered.append(slug)
    for slug in spec:
        if slug not in slugs_ordered:
            slugs_ordered.append(slug)

    plan: list[SkillSectionLoad] = []
    for slug in slugs_ordered:
        try:
            entry = catalog.get(slug)
        except KeyError:
            plan.append(
                SkillSectionLoad(
                    slug=slug,
                    section="",
                    path=f"<missing:{slug}>",
                    read_sha256=None,
                    body_utf8_bytes=0,
                    status="missing_file",
                    fs_call=f"FALLBACK: Use the `{slug}` skill — catalog entry missing",
                )
            )
            continue
        dirname = entry.sot_dirname or slug
        path = _skill_path(slug, dirname)
        rel = _skill_repo_rel(slug, dirname)
        if path is None:
            plan.append(
                SkillSectionLoad(
                    slug=slug,
                    section="",
                    path=rel,
                    read_sha256=None,
                    body_utf8_bytes=0,
                    status="missing_file",
                    fs_call=(
                        f'FALLBACK: fs(op="md_list", sandbox="workspaces", '
                        f'path="universal-llm-gateway/{rel}") — file missing on disk'
                    ),
                )
            )
            continue
        text = path.read_text(encoding="utf-8")
        file_sha = _file_digest(path)
        sections = list(_CORE_REQUIRED.get(slug, ()))
        sections += [s for s in spec.get(slug, ()) if s not in sections]
        for section in sections:
            _append_load(
                plan,
                slug=slug,
                section=section,
                rel=rel,
                file_sha=file_sha,
                text=text,
                already_loaded=already_loaded,
            )
    return plan


def measure_operation_skill_utf8(
    operation: OperationKind,
    *,
    already_loaded: set[LoadedKey] | None = None,
) -> tuple[int, int]:
    """Return (section_body_bytes, plan_overhead_bytes) for loaded sections only."""
    plan = build_skill_section_plan(operation, already_loaded=already_loaded)
    bodies = sum(row.body_utf8_bytes for row in plan if row.status == "ok")
    overhead = sum(len(row.fs_call.encode("utf-8")) for row in plan)
    return bodies, overhead


def format_skill_load_block(
    operation: OperationKind,
    *,
    already_loaded: set[LoadedKey] | None = None,
    detail: Literal["full", "compact"] = "full",
) -> str:
    plan = build_skill_section_plan(operation, already_loaded=already_loaded)
    if detail == "compact":
        named: list[str] = []
        for row in plan:
            if row.status in ("ok", "missing_section", "stale_skip") and row.section:
                label = f"{row.slug}§{row.section}"
                if label not in named:
                    named.append(label)
        section_list = "; ".join(named) if named else "(none)"
        if len(section_list) > 220:
            section_list = section_list[:217] + "..."
        return (
            f"SKILL SECTIONS ({operation}): md_read — {section_list}. "
            f"Full plan: hop_skill_sections.format_skill_load_block({operation!r}).\n"
        )
    lines = [
        f"SKILL SECTIONS ({operation}): md_list/md_read required sections — not full-file skim.",
    ]
    for row in plan:
        if row.status == "missing_file":
            lines.append(f"BLOCKED: {row.fs_call}")
        elif row.status == "missing_section":
            lines.append(f"BLOCKED: {row.fs_call}")
        else:
            lines.append(row.fs_call)
    return "\n".join(lines) + "\n"


__all__ = [
    "LoadedKey",
    "OperationKind",
    "SkillSectionLoad",
    "build_skill_section_plan",
    "format_skill_load_block",
    "measure_operation_skill_utf8",
]
