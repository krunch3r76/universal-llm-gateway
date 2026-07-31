"""Human so-what titles for agent-bus threads (standing ``summary``).

``ThreadDetail.summary`` is the SMS-safe ULG outcome line — not a slug, not
telemetry. Mint intends; CLOSEOUT / charter close refresh or compose DONE —.
"""

from __future__ import annotations

import re

SMS_SUBJECT_MAX = 120
# Transport ceiling for email-bridge /pager/notify (life notify + Fi SMS).
# Machine composers (tick/closeout) still write short; CDP awareness may use the full budget.
SMS_BODY_MAX = 2000

_SO_WHAT_FIELD_RE = re.compile(
    r"(?im)^(?:so_what|ulg_gain)\s*:\s*(.+)$",
)
_DONE_PREFIX = "DONE — "


def clip(text: str, max_len: int) -> str:
    text = " ".join((text or "").split())
    if max_len <= 0 or len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1] + "…"


def extract_so_what_from_body(body: str) -> str | None:
    """Fail-soft: parse ``so_what:`` / ``ulg_gain:`` from DIRECTIVE/CHECKPOINT body."""
    match = _SO_WHAT_FIELD_RE.search(body or "")
    if not match:
        return None
    value = match.group(1).strip()
    return clip(value, SMS_SUBJECT_MAX) if value else None


def resolve_so_what_summary(
    summary: str | None,
    body: str = "",
) -> str | None:
    """Prefer explicit summary; else body ``so_what:`` / ``ulg_gain:``."""
    explicit = (summary or "").strip()
    if explicit:
        return clip(explicit, SMS_SUBJECT_MAX)
    return extract_so_what_from_body(body)


def compose_done_summary(prior: str | None, *, reason: str = "") -> str:
    """Close without erasing so-what: ``DONE — {prior}`` (or reason fallback)."""
    prior = (prior or "").strip()
    if prior.startswith(_DONE_PREFIX):
        return clip(prior, SMS_SUBJECT_MAX)
    # Strip a prior DONE if somehow nested without the em-dash form.
    if prior.upper().startswith("DONE"):
        prior = re.sub(r"(?i)^DONE\s*[—\-:]?\s*", "", prior).strip()
    if prior:
        return clip(f"{_DONE_PREFIX}{prior}", SMS_SUBJECT_MAX)
    reason = (reason or "").strip()
    if reason:
        return clip(f"{_DONE_PREFIX}{reason}", SMS_SUBJECT_MAX)
    return "DONE"


def format_closeout_pager(
    *,
    status: str,
    thread_id: str,
    summary: str | None,
    dispatch_id: str = "",
) -> tuple[str, str]:
    """SMS subject/body for CLOSEOUT — lead with so-what, not DIRECTIVE subject."""
    so_what = (summary or "").strip()
    status_token = (status or "complete").strip() or "complete"
    if so_what:
        subject = clip(f"{so_what} — CLOSEOUT {status_token}", SMS_SUBJECT_MAX)
    else:
        subject = clip(f"CLOSEOUT {status_token} bus:{thread_id}", SMS_SUBJECT_MAX)
    parts = [f"bus:{thread_id}", f"status={status_token}"]
    if dispatch_id:
        parts.append(f"id={dispatch_id}")
    if so_what:
        parts.append(clip(so_what, 80))
    body = clip(" · ".join(parts), SMS_BODY_MAX)
    return subject, body


# Standing-class skips page once until the signature changes (see claim_tick_standing_page).
_STANDING_SKIP_EXACT = frozenset(
    {
        "blocked",
        "revise_cap_exhausted",
        "admission_rejected",
        "admission_transport_error",
        "worker_failed",
        "giw_fleet_busy",
        "stale_window",
    }
)


def standing_skip_signature(
    skipped_by_reason: dict[str, int] | None,
) -> str | None:
    """Return a stable signature when *all* positive skips are standing-class.

    ``None`` means there is a non-standing skip (or no skips) — caller pages
    without standing dedupe (or does not page at all).
    """
    standing: list[str] = []
    for reason, count in sorted((skipped_by_reason or {}).items()):
        if count <= 0:
            continue
        if (
            reason in _STANDING_SKIP_EXACT
            or reason.startswith("stopped:")
            or reason.startswith("no_progress:")
        ):
            standing.append(f"{reason}:{count}")
            continue
        return None
    return "|".join(standing) if standing else None


def tick_should_page(
    *,
    admitted: int,
    closed_count: int,
    skipped_by_reason: dict[str, int] | None,
) -> bool:
    """Suppress pure-idle charter tick SMS (conveyor counters only)."""
    if closed_count > 0 or admitted > 0:
        return True
    skips = skipped_by_reason or {}
    # Material skips worth a human glance — not empty/noop idle.
    interesting = {
        "no_progress:consult_stall",
        "no_progress:checkpoint_missing",
        "no_progress:unchanged_residue",
        "admission_rejected",
        "admission_transport_error",
        "worker_failed",
        "revise_cap_exhausted",
        "giw_fleet_busy",
        "stale_window",
        "blocked",
    }
    for reason, count in skips.items():
        if count <= 0:
            continue
        if reason in interesting or reason.startswith("stopped:"):
            return True
        if reason.startswith("no_progress:"):
            return True
    return False


__all__ = [
    "SMS_BODY_MAX",
    "SMS_SUBJECT_MAX",
    "clip",
    "compose_done_summary",
    "extract_so_what_from_body",
    "format_closeout_pager",
    "resolve_so_what_summary",
    "standing_skip_signature",
    "tick_should_page",
]
