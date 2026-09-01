"""Seat overlay for the cursor-sdk dispatch HOME — prune human register, graft interagent.

The per-dispatch HOME (see :mod:`cursor_home`) copies the operator's
``~/.cursor/plugins`` so ``setting_sources=all`` matches the IDE substrate. That
parity is wrong for two classes of guidance.

The first is a **correctness** problem: the human-facing operator register
(``operator-posture``) teaches a seat to open with Been→Are→Going orientation and
close with "What I need from you" — addressed to a person who is not in the
dispatch loop at all. A headless seat inheriting it addresses its dispatching lead
(a model) as a human.

The second is an **economics** problem: always-applied lead/orchestrator rules are
re-sent on every agent step, so a rule the seat can never act on is charged once per
tool call for the life of the run. ``LEAD_ONLY_PLUGIN_PATHS`` carries that set.

Because the HOME plugin tree is a *copy*, it is the one substrate layer a seat can
actually be excluded from: this module deletes the human-register rule and skill
from that copy and grafts the interagent counterpart in their place, from the seat
overlay SoT under ``cursor-plugins/ulg-ecosystem-seats/cursor-sdk/`` (deliberately
outside the installed plugin, so the IDE never discovers it).

Project rules read live from the workspace cwd cannot be pruned this way — which is
why ``operator-posture`` lives in the plugin tree rather than
``projects/.cursor/rules/``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

_DEFAULT_SOURCE_REPO = "/mnt/torus/projects/universal-llm-gateway"
_OVERLAY_RELPATH = Path("cursor-plugins") / "ulg-ecosystem-seats" / "cursor-sdk"

ECOSYSTEM_PLUGIN_RELPATH = Path("plugins") / "local" / "ulg-ecosystem"

# Human-register / IDE-only guidance pruned from the dispatch HOME plugin copy.
# When a same-named rule exists under the seat overlay, graft replaces it after prune.
PRUNED_PLUGIN_PATHS: tuple[Path, ...] = (
    Path("rules") / "operator-posture_ulg.mdc",
    Path("skills") / "operator-posture",
    Path("rules") / "cdp-operator-proxy_ulg.mdc",  # catalog — not this seat; prune only
    # Mixed rules: each carries a slice the seat does act on (Explore-first recon;
    # landed≠live + drain-gated restart) wrapped in lead doctrine it cannot. Thinned
    # seat variants are grafted back rather than deleting the whole rule.
    Path("rules") / "restart-drain-discipline_ulg.mdc",
    Path("rules") / "skill-surface_ulg.mdc",
)
# 2026-09-01 (alwaysApply rules-thinning G3, agent-bus:9848): the plugin-tree
# originals for the operator-request-front-door / judgment-escalation-ladder /
# lean-context-dispatch-first thinned grafts merged into dispatch-kernel_ulg.mdc.
# Their seat-overlay SoT graft files were removed rather than left to graft under
# a dead filename.
#
# G6 adjudicated 2026-09-01: dispatch-kernel_ulg.mdc and in-flight-work-guard_ulg.mdc
# stay resident, deliberately in neither prune list. Slice audit: 1029 of 1315 tok and
# 457 of 598 tok bind on this seat — Explore-first recon, bug-class-sweep, the
# anthropic/* wall, the non-primary-model gate, bind-then-compose `nest_under`, the
# consult-routing pre-gate, the two-failures stop, and the whole halt-on-overlap guard
# (whose "Lane-B worktrees do not lift the guard" binds harder here than in the IDE,
# since this seat runs in those worktrees). Only 229 / 136 tok are lead-or-CDP-facing,
# and a seat variant cannot buy them: the convention's `**Seat wall.**` + `## Closeout
# duty` + trigger_match_terms cost a measured +179 tok/file, which is why all three
# existing variants are LARGER than their IDE kernels. Best-case projection saves 44
# tok of the ~81K resident prime and forks two more files — the same drift the note
# above had to unwind. That surface is worth cutting UPSTREAM instead, where it also
# pays on IDE steps and closes dispatch-kernel's 215-tok ceiling breach
# (`decision:alwaysapply-rules-thinning-relocation-target`). Numbers: assertion on
# `todo:alwaysapply-rules-thinning`.
# Subset of PRUNED with no same-name graft — must be absent after overlay.
PRUNE_ONLY_PLUGIN_PATHS: tuple[Path, ...] = (
    Path("rules") / "operator-posture_ulg.mdc",
    Path("skills") / "operator-posture",
    Path("rules") / "cdp-operator-proxy_ulg.mdc",
)

# Lead/orchestrator-only rules pruned for context economy, not register correctness.
# Each is always-applied, so the IDE copy re-sends it on every agent step; a headless
# seat never performs the behavior any of them govern. Measured 2026-08-09: the
# resident prime is ~81K tokens per step and the first seven are ~6.6K of it, charged
# across ~3.5K steps/day. Absence here is tolerated — a missing file is a no-op.
#
# The prune floor is behavioral, not byte-ranked: rules a headless seat *does* act on
# stay resident even when large (`python-universal-venv`, `commit-and-git-scope`,
# `presence-discipline`, `shared-checkout-housekeeping`). A rule qualifies here only
# when the seat structurally cannot perform what it governs.
LEAD_ONLY_PLUGIN_PATHS: tuple[Path, ...] = (
    Path("rules") / "expand-growth-loop_ulg.mdc",
    Path("rules") / "claude-ai-cdp-navigation_ulg.mdc",
    Path("rules") / "pager-notify_ulg.mdc",
)
# 2026-09-01: dispatch-in-flight-supremacy / session-abort-authorization merged into
# in-flight-work-guard_ulg.mdc; bind-then-compose-dispatch / anthropic-substrate merged
# into dispatch-kernel_ulg.mdc. Neither merged file is listed here: the seat acts on the
# overlap guard, and the anthropic/* wall plus the bind-then-compose split stopped being
# lead-only the moment a cursor-sdk conductor could spawn nested legs. Slice-level basis
# is the G6 note above PRUNED_PLUGIN_PATHS.


class SeatOverlayConfigError(RuntimeError):
    """Overlay SoT or plugin copy unusable — fail closed rather than leak the human register."""


def seat_overlay_root(*, override: str | None = None) -> Path:
    """Overlay SoT root: ``CURSOR_SDK_SEAT_OVERLAY_ROOT`` else ``<source_repo>/<relpath>``."""
    if override is None:
        override = os.environ.get("CURSOR_SDK_SEAT_OVERLAY_ROOT", "").strip() or None
    if override:
        return Path(override).expanduser()
    repo = Path(
        os.environ.get("GIT_INTEGRATION_SOURCE_REPO", _DEFAULT_SOURCE_REPO)
    ).expanduser()
    return repo / _OVERLAY_RELPATH


def _graft_entries(overlay_root: Path) -> list[tuple[Path, Path]]:
    """(source, plugin-relative destination) pairs for every overlay rule and skill."""
    pairs: list[tuple[Path, Path]] = []
    rules_src = overlay_root / "rules"
    skills_src = overlay_root / "skills"
    for missing in (p for p in (rules_src, skills_src) if not p.is_dir()):
        raise SeatOverlayConfigError(
            f"seat overlay incomplete: {missing} absent under {overlay_root}"
        )
    for rule in sorted(rules_src.glob("*.mdc")):
        pairs.append((rule, Path("rules") / rule.name))
    for skill in sorted(p for p in skills_src.iterdir() if p.is_dir()):
        pairs.append((skill, Path("skills") / skill.name))
    if not pairs:
        raise SeatOverlayConfigError(
            f"seat overlay empty: no rules or skills under {overlay_root}"
        )
    return pairs


def apply_cursor_sdk_seat_overlay(
    cursor_dir: Path,
    *,
    overlay_root: Path | str | None = None,
) -> tuple[list[Path], list[Path]]:
    """Prune the human register and lead-only rules from *cursor_dir*, graft the overlay.

    *cursor_dir* is the dispatch HOME's ``.cursor`` directory (already seeded with
    the plugin copy). Returns ``(pruned, grafted)`` plugin-relative paths — ``pruned``
    spans both :data:`PRUNED_PLUGIN_PATHS` and :data:`LEAD_ONLY_PLUGIN_PATHS`. Raises
    :class:`SeatOverlayConfigError` when the overlay SoT or the plugin copy is
    missing, or when a prune/graft did not take effect — a silent no-op here means
    the seat runs with the human register attached.
    """
    root = (
        seat_overlay_root() if overlay_root is None else Path(overlay_root).expanduser()
    )
    if not root.is_dir():
        raise SeatOverlayConfigError(f"seat overlay root absent: {root}")
    plugin_root = cursor_dir / ECOSYSTEM_PLUGIN_RELPATH
    if not plugin_root.is_dir():
        raise SeatOverlayConfigError(
            f"ecosystem plugin absent from dispatch HOME: {plugin_root} — "
            "install-ecosystem-plugin.sh must have run for the operator"
        )

    graft_pairs = _graft_entries(root)

    pruned: list[Path] = []
    for relpath in (*PRUNED_PLUGIN_PATHS, *LEAD_ONLY_PLUGIN_PATHS):
        target = plugin_root / relpath
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        else:
            continue
        pruned.append(relpath)
        if target.exists():
            raise SeatOverlayConfigError(f"prune failed, still present: {target}")

    grafted: list[Path] = []
    for src, relpath in graft_pairs:
        dst = plugin_root / relpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_dir():
            shutil.rmtree(dst)
        elif dst.exists():
            dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=False)
        else:
            shutil.copy2(src, dst)
        if not dst.exists():
            raise SeatOverlayConfigError(f"graft failed, absent after copy: {dst}")
        grafted.append(relpath)

    logger.info(
        "seat_overlay: pruned=%s grafted=%s plugin_root=%s overlay=%s",
        [str(p) for p in pruned],
        [str(p) for p in grafted],
        plugin_root,
        root,
    )
    return pruned, grafted
