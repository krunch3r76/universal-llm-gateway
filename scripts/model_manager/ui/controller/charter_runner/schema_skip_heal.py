"""Machine self-heal for schema-class charter admit skips.

Helpers live here so ``tick_loop`` stays thin and ``self_heal`` stays under the
modularization yellow band. After two consecutive identical schema reasons the
runner posts a repair CHECKPOINT that re-queues prior gated pickup.
"""

from __future__ import annotations

from pathlib import Path

from universal_logging import get_logger

from . import bus_client
from .caps import CapStore
from .checkpoint_admit_gate import SCHEMA_REASONS, validate_checkpoint_for_admit
from .eligibility import Decision
from .self_heal_checkpoint import (
    build_self_heal_checkpoint,
    pickup_survives_round_trip,
)

logger = get_logger(__name__)

_SCHEMA_SKIP_THRESHOLD = 2


def _schema_skip_dir() -> Path:
    return Path.home() / ".local" / "share" / "charter-runner" / "schema-skips"


def _schema_skip_path(root_id: str) -> Path:
    return _schema_skip_dir() / f"{root_id}.count"


def _read_schema_skip(root_id: str) -> tuple[str | None, int]:
    """Return ``(last_reason, count)``; missing/corrupt store ⇒ ``(None, 0)``."""
    path = _schema_skip_path(root_id)
    if not path.is_file():
        return None, 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        reason = (lines[0] if lines else "").strip() or None
        count = max(0, int(lines[1])) if len(lines) > 1 else 0
        return reason, count
    except (OSError, ValueError, IndexError):
        return None, 0


def _write_schema_skip(root_id: str, reason: str, count: int) -> None:
    path = _schema_skip_path(root_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{reason}\n{count}\n", encoding="utf-8")


def _clear_schema_skip(root_id: str) -> None:
    _schema_skip_path(root_id).unlink(missing_ok=True)


def _increment_schema_skip(root_id: str, reason: str) -> int:
    """Bump consecutive schema-skip count; reason change resets to 1."""
    last_reason, count = _read_schema_skip(root_id)
    if last_reason != reason:
        count = 0
    count += 1
    _write_schema_skip(root_id, reason, count)
    return count


async def try_self_heal_schema_skip(
    decision: Decision,
    *,
    caps: CapStore,
) -> bool:
    """Post a machine repair CHECKPOINT after two consecutive schema-class skips.

    ``decision.parsed`` is required (populated on ``missing_sections`` et al.);
    ``parse_failed`` leaves it ``None`` and returns False. Never posts when
    ``pickup_survives_round_trip`` fails — a repair must not drop gated pickup.
    ``caps`` is reserved for symmetry with the incomplete-window heal path.
    """
    _ = caps
    reason = decision.reason
    if reason not in SCHEMA_REASONS:
        return False
    if decision.parsed is None:
        return False
    count = _increment_schema_skip(decision.root_id, reason)
    if count < _SCHEMA_SKIP_THRESHOLD:
        return False
    subject, body = build_self_heal_checkpoint(
        prior=decision.parsed,
        window_index=0,
        worker_thread="",
        reason=reason,
        root_id=decision.root_id,
    )
    ok, want, got = pickup_survives_round_trip(decision.parsed, body)
    if not ok:
        logger.warning(
            "charter-runner schema self-heal skipped root=%s reason=%s — "
            "pickup round-trip failed want=%r got=%r",
            decision.root_id,
            reason,
            want,
            got,
        )
        return False
    verdict = validate_checkpoint_for_admit(body)
    if not verdict.ok:
        logger.error(
            "charter-runner schema self-heal CHECKPOINT invalid root=%s "
            "reason=%s hint=%s — skip post",
            decision.root_id,
            verdict.reason,
            verdict.fix_hint,
        )
        return False
    try:
        await bus_client.post_root_checkpoint(
            decision.root_id, subject=subject, body=body
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "charter-runner schema self-heal CHECKPOINT post failed root=%s",
            decision.root_id,
        )
        return False
    _clear_schema_skip(decision.root_id)
    return True


__all__ = ["try_self_heal_schema_skip"]
