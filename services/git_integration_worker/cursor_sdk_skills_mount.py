"""Materialize ``team_dispatch(skills=[...])`` into the dispatch HOME user layer.

Who calls: ``_run_sdk_sync`` for every ``team_dispatch(op=generate, seat=cursor-sdk)``
that carried ``skills=``, after :func:`cursor_home.setup_cursor_dispatch_home` and
before the SDK bridge launches.

``cursor-sdk==1.0.31`` has no ``skills`` field on ``LocalAgentOptions`` — the SDK
never names a skill at all. ``cursor-agent`` discovers them from the filesystem,
scoped by ``setting_sources`` (``("all",)`` here, so ``user`` and ``plugins`` are in).
Mounting a skill therefore means putting its body on a path the seat will read, and
the per-dispatch HOME is the one layer this worker owns outright.

**Mounting is two obligations, not one.** Staging makes a body *discoverable*;
Cursor skills are description-gated, so a ``Use the {slug} skill`` line is what makes
the harness *activate* it (:mod:`cursor_sdk_packet` emits those). Staging without the
invoke leaves an unread file; the invoke without staging is the silent no-op this
module exists to end.

Skills already discoverable through the HOME plugin census or the workspace
``.cursor/skills`` tree are deliberately *not* staged: two SKILL.md files sharing one
``name:`` frontmatter is a duplicate-slug hazard, and the seat can already read them.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from skills_mount.cursor_fs import classify_cursor_skills
from universal_logging import get_logger

from services.git_integration_worker.cursor_seat_overlay import (
    ECOSYSTEM_PLUGIN_RELPATH,
)

logger = get_logger(__name__)

#: User-layer skills dir inside a dispatch HOME's ``.cursor``. Cursor reads this
#: alongside its own ``skills-cursor`` bundle; ``setup_cursor_dispatch_home`` copies
#: ``rules/`` and ``plugins/`` but never created this one.
HOME_SKILLS_DIRNAME = "skills"

Disposition = str
"""``staged`` (copied here) | ``preexisting`` (already readable) | ``unresolved``."""


@dataclass(frozen=True, slots=True)
class SkillMountRow:
    """Per-slug mount outcome, one row per requested skill."""

    requested_id: str
    canonical_slug: str
    disposition: Disposition
    layer: str | None = None
    dest: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SkillsMountResult:
    """Aggregate mount outcome for one dispatch."""

    rows: tuple[SkillMountRow, ...]

    @property
    def staged_slugs(self) -> tuple[str, ...]:
        return tuple(r.canonical_slug for r in self.rows if r.disposition == "staged")

    @property
    def preexisting_slugs(self) -> tuple[str, ...]:
        return tuple(
            r.canonical_slug for r in self.rows if r.disposition == "preexisting"
        )

    @property
    def unresolved_slugs(self) -> tuple[str, ...]:
        return tuple(
            r.canonical_slug for r in self.rows if r.disposition == "unresolved"
        )

    @property
    def mounted_slugs(self) -> tuple[str, ...]:
        """Everything the seat can now read — staged plus already-present."""
        return tuple(
            r.canonical_slug
            for r in self.rows
            if r.disposition in ("staged", "preexisting")
        )

    def as_event_payload(self) -> list[dict[str, str | None]]:
        return [
            {
                "requested_id": r.requested_id,
                "canonical_slug": r.canonical_slug,
                "disposition": r.disposition,
                "layer": r.layer,
                "reason": r.reason,
            }
            for r in self.rows
        ]


def discoverable_skill_dirs(
    cursor_dir: Path,
    *,
    workspace_roots: Sequence[Path] = (),
) -> tuple[Path, ...]:
    """Directories a seat already reads skills from, for the skip-if-present check.

    The HOME plugin census first (copied by ``setup_cursor_dispatch_home``), then each
    workspace root's ``.cursor/skills`` — the project layer ``setting_sources`` picks
    up from ``local.cwd`` and ``local.dirs``.
    """
    dirs: list[Path] = [cursor_dir / ECOSYSTEM_PLUGIN_RELPATH / "skills"]
    for root in workspace_roots:
        dirs.append(Path(root) / ".cursor" / "skills")
    return tuple(dirs)


def _already_discoverable(slug: str, dirs: Sequence[Path]) -> Path | None:
    for base in dirs:
        candidate = base / slug / "SKILL.md"
        if candidate.is_file():
            return candidate
    return None


def stage_dispatch_skills(
    cursor_dir: Path,
    slugs: Sequence[str] | None,
    *,
    source_repo: Path,
    workspace_roots: Sequence[Path] = (),
) -> SkillsMountResult:
    """Copy requested skill bodies into ``{cursor_dir}/skills/{slug}/SKILL.md``.

    Args:
        cursor_dir: The dispatch HOME's ``.cursor`` directory.
        slugs: Requested skill ids from the admit payload; ``None``/empty is a no-op.
        source_repo: Hub checkout. Resolution must run against the hub because
            ``life_local`` bodies live under gitignored ``.claude/`` and are absent
            from every Lane-B worktree.
        workspace_roots: Trees whose ``.cursor/skills`` the seat already reads
            (``dispatch_workspace``, ``workspace_root``).

    Returns:
        One row per requested slug. Unresolvable slugs are recorded, never raised:
        Stargate fail-closes at admit, so a miss here means the body moved between
        admit and run — worth an event, not worth killing a dispatch that may not
        depend on it.
    """
    requested = [str(s).strip() for s in (slugs or ()) if str(s or "").strip()]
    if not requested:
        return SkillsMountResult(rows=())

    resolution = classify_cursor_skills(requested, repo_root=source_repo)
    present_dirs = discoverable_skill_dirs(
        cursor_dir, workspace_roots=workspace_roots
    )
    staged_root = cursor_dir / HOME_SKILLS_DIRNAME

    rows: list[SkillMountRow] = []
    for slug, reason in resolution.unresolved:
        rows.append(
            SkillMountRow(
                requested_id=slug,
                canonical_slug=slug,
                disposition="unresolved",
                reason=reason,
            )
        )

    for sot in resolution.resolved:
        existing = _already_discoverable(sot.canonical_slug, present_dirs)
        if existing is not None:
            rows.append(
                SkillMountRow(
                    requested_id=sot.requested_id,
                    canonical_slug=sot.canonical_slug,
                    disposition="preexisting",
                    layer=sot.layer,
                    dest=str(existing),
                )
            )
            continue

        dest = staged_root / sot.canonical_slug / "SKILL.md"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sot.path, dest)
        except OSError as exc:
            rows.append(
                SkillMountRow(
                    requested_id=sot.requested_id,
                    canonical_slug=sot.canonical_slug,
                    disposition="unresolved",
                    layer=sot.layer,
                    reason=f"stage failed: {exc}",
                )
            )
            continue
        if not dest.is_file():
            rows.append(
                SkillMountRow(
                    requested_id=sot.requested_id,
                    canonical_slug=sot.canonical_slug,
                    disposition="unresolved",
                    layer=sot.layer,
                    reason=f"stage verified absent after copy: {dest}",
                )
            )
            continue
        rows.append(
            SkillMountRow(
                requested_id=sot.requested_id,
                canonical_slug=sot.canonical_slug,
                disposition="staged",
                layer=sot.layer,
                dest=str(dest),
            )
        )

    result = SkillsMountResult(rows=tuple(rows))
    logger.info(
        "cursor_sdk skills mount: staged=%s preexisting=%s unresolved=%s home=%s",
        list(result.staged_slugs),
        list(result.preexisting_slugs),
        list(result.unresolved_slugs),
        staged_root,
    )
    return result
