"""Structural CHECKPOINT predicates shared by advisory profiles and auto-stamp wiring.

Bootstrap structural: CHECKPOINT subject with same-thread ``supersedes_turn`` on a
thread that lacks ``role:root``. Steady-state structural: same subject and supersede
when ``role:root`` is already present. ``AGENT_BUS_CHECKPOINT_AUTO_STAMP`` defaults
off; when truthy, bootstrap detection may stamp ``role:root`` (undo via
``remove_tags``). Consumer authority differs: bootstrap subject prefix is necessary
for auto-stamp; steady-state detection corroborates advisory silence only.
"""

from __future__ import annotations

import os

from .checkpoint_projection import is_checkpoint_subject
from .thread_classification import ROLE_ROOT_TAG

# Env flag for P1 auto-stamp (default off). Undo via remove_tags(["role:root"]).
_CHECKPOINT_AUTO_STAMP_TRUTHY = frozenset({"1", "true", "yes", "on"})


def checkpoint_auto_stamp_enabled() -> bool:
    """Return True when ``AGENT_BUS_CHECKPOINT_AUTO_STAMP`` is truthy (default off)."""
    raw = os.environ.get("AGENT_BUS_CHECKPOINT_AUTO_STAMP", "").strip().lower()
    return raw in _CHECKPOINT_AUTO_STAMP_TRUTHY


def is_bootstrap_structural_checkpoint(
    *,
    subject: str,
    thread_tags: list[str],
    supersedes_turn: int | None,
) -> bool:
    """Pre-``role:root`` structural CP: CHECKPOINT subject ∧ same-thread supersede."""
    if supersedes_turn is None:
        return False
    if not is_checkpoint_subject(subject):
        return False
    normalized = {t.strip().lower() for t in thread_tags if t and str(t).strip()}
    return ROLE_ROOT_TAG not in normalized


def is_steady_state_structural_checkpoint(
    *,
    subject: str,
    thread_tags: list[str],
    supersedes_turn: int | None,
) -> bool:
    """Steady-state structural CP: ``role:root`` ∧ CHECKPOINT subject ∧ supersede."""
    if supersedes_turn is None:
        return False
    if not is_checkpoint_subject(subject):
        return False
    normalized = {t.strip().lower() for t in thread_tags if t and str(t).strip()}
    return ROLE_ROOT_TAG in normalized


def is_structural_checkpoint(
    *,
    subject: str,
    thread_tags: list[str],
    supersedes_turn: int | None,
) -> bool:
    """True for bootstrap or steady-state structural CHECKPOINT (not BIRTH-shaped)."""
    return is_bootstrap_structural_checkpoint(
        subject=subject,
        thread_tags=thread_tags,
        supersedes_turn=supersedes_turn,
    ) or is_steady_state_structural_checkpoint(
        subject=subject,
        thread_tags=thread_tags,
        supersedes_turn=supersedes_turn,
    )


def is_birth_shaped_checkpoint(
    *,
    subject: str,
    supersedes_turn: int | None,
) -> bool:
    """CHECKPOINT subject without same-thread supersede (enrolled-lane silence retained)."""
    return supersedes_turn is None and is_checkpoint_subject(subject)


def is_standing_root_thread(thread_tags: list[str] | None) -> bool:
    """True when the thread carries the ``role:root`` standing-root tag."""
    normalized = {t.strip().lower() for t in (thread_tags or []) if t and str(t).strip()}
    return ROLE_ROOT_TAG in normalized


def should_auto_derive_supersedes_turn(
    *,
    subject: str,
    thread_tags: list[str] | None,
    turn_number: int | None,
    turn_id_alias: int | None,
) -> bool:
    """True when send/reply may omit ``supersedes_turn`` on a standing-root CHECKPOINT."""
    if turn_number is not None or turn_id_alias is not None:
        return False
    return is_checkpoint_subject(subject) and is_standing_root_thread(thread_tags)


__all__ = [
    "checkpoint_auto_stamp_enabled",
    "is_birth_shaped_checkpoint",
    "is_bootstrap_structural_checkpoint",
    "is_steady_state_structural_checkpoint",
    "is_standing_root_thread",
    "is_structural_checkpoint",
    "should_auto_derive_supersedes_turn",
]
