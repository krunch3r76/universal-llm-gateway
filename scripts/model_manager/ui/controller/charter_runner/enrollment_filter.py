"""Ledger-driven enrollment filter — old-tick isolation for kernel-migrated roots (P2C-AC5)."""

from __future__ import annotations

from universal_logging import get_logger

from .admission.typed_work_item import typed_record_valid
from .root_ledger import load_all_roots, open_default_ledger
from .seed_phase1 import PHASE1_SEEDS
from .telemetry import emit_enrollment_filtered

logger = get_logger(__name__)

_BOOT_MIGRATED = frozenset(seed.root_id for seed in PHASE1_SEEDS)
_migrated_cache: frozenset[str] = _BOOT_MIGRATED
_old_tick_violations: dict[str, int] = {rid: 0 for rid in _BOOT_MIGRATED}

# Back-compat alias for tests that pin Phase-1 seed ids.
MIGRATED_ROOTS = _BOOT_MIGRATED


def refresh_migrated_roots_cache() -> frozenset[str]:
    """Reload kernel-migrated root ids from the durable ledger (sole writer: kernel)."""
    global _migrated_cache
    try:
        conn = open_default_ledger()
        try:
            rows = load_all_roots(conn)
            ids = frozenset(
                row.root_id for row in rows if typed_record_valid(row)
            )
            if ids:
                _migrated_cache = ids
        finally:
            conn.close()
    except Exception:
        logger.exception(
            "charter-runner enrollment filter: ledger read failed — keeping cache"
        )
    return _migrated_cache


def is_kernel_migrated(root_id: str) -> bool:
    return root_id in _migrated_cache


def old_tick_admit_count(root_id: str) -> int:
    return _old_tick_violations.get(root_id, 0)


async def record_old_tick_admit_blocked(root_id: str) -> None:
    """Old-path admit reached a ledger-migrated root — count and emit (P2-AC4 observable)."""
    if root_id not in _migrated_cache:
        return
    count = _old_tick_violations.get(root_id, 0) + 1
    _old_tick_violations[root_id] = count
    logger.warning(
        "charter-runner old-tick admit blocked for kernel-migrated root=%s count=%d",
        root_id,
        count,
    )
    await emit_enrollment_filtered(root=root_id, reason="old_tick_admit_blocked")


__all__ = [
    "MIGRATED_ROOTS",
    "is_kernel_migrated",
    "old_tick_admit_count",
    "record_old_tick_admit_blocked",
    "refresh_migrated_roots_cache",
]
