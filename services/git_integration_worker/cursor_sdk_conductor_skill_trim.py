"""Drop unlisted skill directories from a conductor dispatch HOME plugin census.

Who calls: ``_run_sdk_sync`` after ``setup_cursor_dispatch_home`` (seat overlay)
and ``stage_dispatch_skills``. The HOME is a full plugin copy; this deletes
skill directories that are not in the conductor keep-set. Other contracts are
untouched. ``CURSOR_SDK_CONDUCTOR_SKILL_TRIM=0`` restores the full census.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_seat_overlay import (
    ECOSYSTEM_PLUGIN_RELPATH,
)

logger = get_logger(__name__)

_ENV = "CURSOR_SDK_CONDUCTOR_SKILL_TRIM"

# Static conductor rows plus slugs observed in conductor closeouts
# (tmp/reviews/closeouts, 2026-09-05 .. 2026-10-01, 781 rows, dispatch-home
# skill paths). Admit ``skills=`` is unioned at call time, not here.
CONDUCTOR_SKILL_KEEP: frozenset[str] = frozenset(
    {
        "abstraction-layering",
        "agent-bus-discipline",
        "architecture-invariants",
        "bind-then-compose-dispatch",
        "cdp-operator-proxy",
        "cheap-recon-before-escalation",
        "checkpoint-discipline",
        "checkout-kernel",
        "claude-ai-cdp-navigation",
        "conductor",
        "consult-routing",
        "continuity-thread-shaping",
        "cortex",
        "cortex-orientation",
        "cursor-sdk-instruction-standard",
        "directive-authoring-standard",
        "dispatch-report-discipline",
        "dispatch-shape",
        "dispatch-workflow",
        "fs",
        "git-posture",
        "handoff-packet-authoring",
        "hypothesize-simulate",
        "interagent-posture",
        "judgment-escalation-ladder",
        "lead-agent-git-integration",
        "liaison",
        "life-operator-do-chain",
        "mission-operator",
        "orchestration-lanes",
        "pager-notify",
        "reasoning-posture",
        "residual-imprint",
        "retrieval-before-authoring",
        "score-play",
        "ulg-for-llms",
        "work-item-seed-path",
    }
)


def conductor_skill_trim_enabled() -> bool:
    """Default on. ``0`` / ``false`` / ``no`` / ``off`` restores the full census."""
    raw = os.environ.get(_ENV, "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def trim_conductor_skill_catalog(
    cursor_dir: Path,
    *,
    contract: str | None,
    extra_slugs: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Remove plugin skill dirs outside the keep-set. Returns removed slug names.

    A missing keep-set slug is a warning; the home is still built. Non-conductor
    contracts and a disabled switch are no-ops.
    """
    if (contract or "").strip() != "conductor":
        return ()
    if not conductor_skill_trim_enabled():
        return ()
    skills_dir = Path(cursor_dir) / ECOSYSTEM_PLUGIN_RELPATH / "skills"
    keep = set(CONDUCTOR_SKILL_KEEP)
    for slug in extra_slugs or ():
        text = str(slug or "").strip()
        if text:
            keep.add(text)
    if not skills_dir.is_dir():
        logger.warning(
            "conductor skill trim: skills dir absent at %s; home left as copied",
            skills_dir,
        )
        for slug in sorted(keep):
            logger.warning(
                "conductor skill trim: keep-set slug %s missing from source tree",
                slug,
            )
        return ()
    present = {p.name for p in skills_dir.iterdir() if p.is_dir()}
    for slug in sorted(keep - present):
        logger.warning(
            "conductor skill trim: keep-set slug %s missing from source tree",
            slug,
        )
    removed: list[str] = []
    for name in sorted(present - keep):
        target = skills_dir / name
        try:
            shutil.rmtree(target)
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning(
                "conductor skill trim: failed to remove %s: %s", target, exc
            )
            continue
        removed.append(name)
    return tuple(removed)
