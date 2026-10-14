"""Poll / closeout status helpers for journal projection (K29 d)."""

from __future__ import annotations

from claude_bundles.journal_lesson import WriterMode, writer_mode

_PROVIDER_ERROR_PREFIX = "provider_error"


def closeout_indicates_provider_error(closeout_text: str) -> bool:
    """True when the closeout's first substantive lines signal provider failure."""
    for line in (closeout_text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lowered = stripped.lower()
        if lowered.startswith("status:"):
            continue
        return lowered.startswith(_PROVIDER_ERROR_PREFIX)
    return False


def projection_poll_is_delay(*, tracker_status: str, wait_status: str | None = None) -> bool:
    """Running / predicate_unmet / no_new_turn are not capture failure."""
    status = (tracker_status or "").strip().lower()
    if status == "running":
        return True
    ws = (wait_status or "").strip().lower()
    return ws in {"predicate_unmet", "no_new_turn"}


def seat_may_fallback_after_projector_failure(
    *,
    journal_text: str,
    closeout_text: str | None = None,
    tracker_status: str | None = None,
    wait_status: str | None = None,
    capacity_error: bool = False,
    append_failed: bool = False,
) -> bool:
    """Seat appends again only on failed projection, not on poll delay."""
    if writer_mode(journal_text) != "projector":
        return False
    if projection_poll_is_delay(
        tracker_status=tracker_status or "", wait_status=wait_status
    ):
        return False
    if capacity_error or append_failed:
        return True
    if closeout_text and closeout_indicates_provider_error(closeout_text):
        return True
    return False


def writer_mode_after_failure(journal_text: str, *, failed: bool) -> WriterMode:
    if failed and writer_mode(journal_text) == "projector":
        return "seat"
    return writer_mode(journal_text)
